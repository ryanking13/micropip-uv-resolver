#[cfg(not(target_os = "emscripten"))]
fn main() {}

#[cfg(target_os = "emscripten")]
mod emscripten {
    use std::collections::{BTreeMap, BTreeSet};
    use std::ffi::{CStr, CString, c_char};
    use std::str::FromStr;
    use std::sync::{Arc, Once};

    use serde::{Deserialize, Serialize};
    use tracing_subscriber::prelude::*;
    use uv_client::{OwnedArchive, SimpleDetailMetadata};
    use uv_configuration::{
        BuildOptions, Constraints, DependencyModifiers, NoBinary, NoBuild, TargetTriple,
    };
    use uv_distribution::ArchiveMetadata;
    use uv_distribution_types::{
        BuiltDist, Dist, IndexCapabilities, IndexLocations, IndexMetadata, InstalledDist,
        RequiresPython, ResolvedDist,
    };
    use uv_git::GitResolver;
    use uv_normalize::PackageName;
    use uv_pep440::Version;
    use uv_pep508::{MarkerEnvironment, MarkerEnvironmentBuilder, Requirement};
    use uv_platform_tags::{Tags, TagsOptions};
    use uv_pypi_types::{
        Conflicts, HashDigest, PypiSimpleDetail, ResolutionMetadata, ResolverMarkerEnvironment,
    };
    use uv_resolver::{
        AllowedYanks, Exclusions, InMemoryIndex, Manifest, MetadataResponse, OptionsBuilder,
        Preference, Preferences, PythonRequirement, Resolver, ResolverEnvironment,
        ResolverProvider, VersionMap, VersionsResponse,
    };
    use uv_resolver_types::{MetadataUnavailable, ResolutionGraphNode};
    use uv_types::{EmptyInstalledPackages, HashStrategy};

    #[derive(Deserialize)]
    #[serde(rename_all = "snake_case")]
    struct ResolveInput {
        requirements: Vec<String>,
        python_version: String,
        python_platform: String,
        /// Simple JSON API index URL (without the package name). Defaults to PyPI.
        #[serde(default)]
        index_url: Option<String>,
        /// Versions to prefer when they satisfy the requirements (e.g. the
        /// versions in the host's lock file).
        #[serde(default)]
        preferences: Vec<PinInput>,
    }

    #[derive(Deserialize)]
    struct PinInput {
        name: String,
        version: String,
    }

    #[derive(Serialize)]
    struct ResolveOutput {
        ok: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        packages: Option<Vec<PackagePin>>,
        #[serde(skip_serializing_if = "Option::is_none")]
        roots: Option<Vec<String>>,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    }

    #[derive(Serialize)]
    struct PackagePin {
        name: String,
        version: String,
        dependencies: Vec<String>,
        /// The selected wheel, if the package comes from a registry.
        #[serde(skip_serializing_if = "Option::is_none")]
        url: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        filename: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        sha256: Option<String>,
    }

    struct WasmResolverProvider {
        index_url: String,
        tags: Tags,
        requires_python: RequiresPython,
        allowed_yanks: AllowedYanks,
        build_options: BuildOptions,
        index_locations: IndexLocations,
    }

    impl WasmResolverProvider {
        fn package_url(&self, name: &PackageName) -> String {
            format!("{}/{name}/", self.index_url.trim_end_matches('/'))
        }

        fn fetch(url: &str, accept: &str) -> Result<(u16, Vec<u8>), String> {
            let url = CString::new(url).map_err(|error| error.to_string())?;
            let accept = CString::new(accept).map_err(|error| error.to_string())?;
            let mut length = 0_u32;
            let mut status = 0_u32;
            // SAFETY: The pointers are valid for the duration of this synchronous JSPI import.
            let bytes = unsafe {
                uv_js_fetch(
                    url.as_ptr(),
                    accept.as_ptr(),
                    &mut length,
                    &mut status,
                )
            };
            if bytes.is_null() {
                return Err("fetch returned a null body".to_string());
            }
            let length = usize::try_from(length).map_err(|error| error.to_string())?;
            // SAFETY: JavaScript allocated exactly `length` bytes using Emscripten's malloc.
            let body = unsafe { Vec::from_raw_parts(bytes, length, length) };
            let status = u16::try_from(status).map_err(|error| error.to_string())?;
            Ok((status, body))
        }
    }

    impl ResolverProvider for WasmResolverProvider {
        async fn get_package_versions(
            &self,
            package_name: &PackageName,
            _index: Option<&IndexMetadata>,
        ) -> uv_resolver::PackageVersionsResult {
            let url = self.package_url(package_name);
            let Ok((status, body)) = Self::fetch(&url, "application/vnd.pypi.simple.v1+json") else {
                return Ok(VersionsResponse::NotFound);
            };
            if status == 404 {
                return Ok(VersionsResponse::NotFound);
            }
            if !(200..300).contains(&status) {
                return Ok(VersionsResponse::NotFound);
            }
            let Ok(detail) = serde_json::from_slice::<PypiSimpleDetail>(&body) else {
                return Ok(VersionsResponse::NotFound);
            };
            let Ok(base) = url::Url::parse(&url) else {
                return Ok(VersionsResponse::NotFound);
            };
            let metadata = SimpleDetailMetadata::from_pypi_files(
                detail.files,
                package_name,
                detail.project_status,
                &base,
            );
            let Ok(metadata) = OwnedArchive::from_unarchived(&metadata) else {
                return Ok(VersionsResponse::NotFound);
            };
            let Some(index) = self.index_locations.default_index() else {
                return Ok(VersionsResponse::NoIndex);
            };
            Ok(VersionsResponse::Found(vec![VersionMap::from_simple_metadata(
                metadata,
                package_name,
                index.url().clone(),
                Some(self.tags.clone()),
                self.requires_python.clone(),
                self.allowed_yanks.clone(),
                HashStrategy::default(),
                None,
                None,
                None,
                &self.build_options,
                None,
            )]))
        }

        async fn get_or_build_wheel_metadata(
            &self,
            dist: &Dist,
        ) -> uv_resolver::WheelMetadataResult {
            let Dist::Built(BuiltDist::Registry(dist)) = dist else {
                return Ok(MetadataResponse::Unavailable(MetadataUnavailable::Network(
                    reqwest::StatusCode::NOT_IMPLEMENTED,
                )));
            };
            let wheel = dist.best_wheel();
            if wheel.file.dist_info_metadata.is_none() {
                return Ok(MetadataResponse::Unavailable(MetadataUnavailable::Network(
                    reqwest::StatusCode::NOT_IMPLEMENTED,
                )));
            }
            let Ok(url) = wheel.file.url.to_url() else {
                return Ok(MetadataResponse::Unavailable(MetadataUnavailable::Network(
                    reqwest::StatusCode::BAD_GATEWAY,
                )));
            };
            let metadata_url = format!("{url}.metadata");
            let Ok((status, body)) = Self::fetch(&metadata_url, "text/plain") else {
                return Ok(MetadataResponse::Unavailable(MetadataUnavailable::Network(
                    reqwest::StatusCode::BAD_GATEWAY,
                )));
            };
            let status = reqwest::StatusCode::from_u16(status)
                .unwrap_or(reqwest::StatusCode::BAD_GATEWAY);
            if !status.is_success() {
                return Ok(MetadataResponse::Unavailable(MetadataUnavailable::Network(status)));
            }
            match ResolutionMetadata::parse_metadata(&body) {
                Ok(metadata) => Ok(MetadataResponse::Found(ArchiveMetadata::from_metadata23(metadata))),
                Err(error) => Ok(MetadataResponse::Unavailable(
                    MetadataUnavailable::InvalidMetadata(Arc::new(error)),
                )),
            }
        }

        async fn get_installed_metadata(
            &self,
            _dist: &InstalledDist,
        ) -> uv_resolver::WheelMetadataResult {
            Ok(MetadataResponse::Unavailable(MetadataUnavailable::Network(
                reqwest::StatusCode::NOT_IMPLEMENTED,
            )))
        }

        fn with_reporter(self, _reporter: Arc<dyn uv_distribution::Reporter>) -> Self {
            self
        }
    }

    unsafe extern "C" {
        fn uv_js_fetch(
            url: *const c_char,
            accept: *const c_char,
            out_length: *mut u32,
            out_status: *mut u32,
        ) -> *mut u8;
    }

    fn output(value: ResolveOutput) -> *mut c_char {
        let json = serde_json::to_string(&value).unwrap_or_else(|error| {
            format!(r#"{{\"ok\":false,\"error\":\"{error}\"}}"#)
        });
        CString::new(json)
            .unwrap_or_default()
            .into_raw()
    }

    fn initialize_tracing() {
        static TRACING: Once = Once::new();

        TRACING.call_once(|| {
            let filter = tracing_subscriber::filter::Targets::new()
                .with_target("uv_resolver", tracing::level_filters::LevelFilter::DEBUG)
                .with_default(tracing::level_filters::LevelFilter::WARN);
            let _ = tracing_subscriber::registry()
                .with(
                    tracing_subscriber::fmt::layer()
                        .with_ansi(false)
                        .without_time()
                        .with_target(true)
                        .with_level(true)
                        .with_writer(std::io::stderr)
                        .with_filter(filter),
                )
                .try_init();
        });
    }

    fn resolve(request: ResolveInput) -> Result<(Vec<PackagePin>, Vec<String>), String> {
        initialize_tracing();
        let target = serde_json::from_value::<TargetTriple>(serde_json::Value::String(
            request.python_platform.clone(),
        ))
        .map_err(|error| error.to_string())?;
        let version = Version::from_str(&format!("{}.0", request.python_version))
            .map_err(|error| error.to_string())?;
        let major_minor = request
            .python_version
            .split_once('.')
            .ok_or_else(|| "python_version must be major.minor".to_string())?;
        let major = major_minor.0.parse::<u8>().map_err(|error| error.to_string())?;
        let minor = major_minor.1.parse::<u8>().map_err(|error| error.to_string())?;
        let base_marker_environment = MarkerEnvironment::try_from(MarkerEnvironmentBuilder {
            implementation_name: "cpython",
            implementation_version: &format!("{}.{}.0", major, minor),
            os_name: "posix",
            platform_machine: "x86_64",
            platform_python_implementation: "CPython",
            platform_release: "",
            platform_system: "Linux",
            platform_version: "",
            python_full_version: &format!("{}.{}.0", major, minor),
            python_version: &request.python_version,
            sys_platform: "linux",
        })
        .map_err(|error| error.to_string())?;
        let marker_environment = target.markers(base_marker_environment);
        let tags = Tags::from_env(
            target.platform(),
            (major, minor),
            "cpython",
            (major, minor),
            TagsOptions {
                manylinux_compatible: target.manylinux_compatible(),
                gil_disabled: false,
                debug_enabled: false,
                is_cross: true,
            },
        )
        .map_err(|error| error.to_string())?;
        let requirements = request
            .requirements
            .iter()
            .map(|requirement| Requirement::from_str(requirement).map(Into::into))
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| error.to_string())?;
        let environment = ResolverEnvironment::specific(ResolverMarkerEnvironment::from(
            marker_environment.clone(),
        ));
        let preferences = Preferences::from_iter(
            request.preferences.iter().filter_map(|pin| {
                Some(Preference::from_locked(
                    PackageName::from_str(&pin.name).ok()?,
                    Version::from_str(&pin.version).ok()?,
                    None,
                    vec![],
                ))
            }),
            &environment,
        );
        let manifest = Manifest::new(
            requirements,
            Constraints::default(),
            DependencyModifiers::default(),
            preferences,
            None,
            BTreeSet::new(),
            Exclusions::default(),
            vec![],
        );
        let requires_python = RequiresPython::greater_than_equal_version(&version);
        let python_requirement =
            PythonRequirement::from_marker_environment(&marker_environment, requires_python.clone());
        let options = OptionsBuilder::new().build();
        let index_locations = IndexLocations::default();
        let provider = WasmResolverProvider {
            index_url: request
                .index_url
                .clone()
                .unwrap_or_else(|| "https://pypi.org/simple".to_string()),
            tags: tags.clone(),
            requires_python,
            allowed_yanks: AllowedYanks::from_manifest(&manifest, &environment, options.dependency_mode),
            // Wheels only: sdists can't be built here, and the result must name a wheel.
            build_options: BuildOptions::new(NoBinary::None, NoBuild::All),
            index_locations: index_locations.clone(),
        };
        let index = InMemoryIndex::default();
        let hasher = HashStrategy::default();
        let resolver = Resolver::new_custom_io(
            manifest,
            options,
            &hasher,
            environment,
            &marker_environment,
            Some(tags),
            &python_requirement,
            Conflicts::default(),
            &index,
            &GitResolver::default(),
            &IndexCapabilities::default(),
            &index_locations,
            provider,
            EmptyInstalledPackages,
        );
        let output = futures::executor::block_on(resolver.resolve()).map_err(|error| error.to_string())?;
        let mut packages = BTreeMap::<String, (String, BTreeSet<String>)>::new();
        let mut files = BTreeMap::<String, (Option<String>, String, Option<String>)>::new();
        for (_, distribution) in output.base_dists() {
            packages.insert(
                distribution.name.to_string(),
                (distribution.version.to_string(), BTreeSet::new()),
            );
            if let ResolvedDist::Installable { dist, .. } = &distribution.dist {
                if let Dist::Built(BuiltDist::Registry(registry)) = dist.as_ref() {
                    let wheel = registry.best_wheel();
                    let url = wheel.file.url.to_url().ok().map(|url| url.to_string());
                    let sha256 = wheel
                        .file
                        .hashes
                        .iter()
                        .find(|hash| matches!(hash, HashDigest::Sha256(_)))
                        .map(|hash| hash.digest().to_string());
                    files.insert(
                        distribution.name.to_string(),
                        (url, wheel.filename.to_string(), sha256),
                    );
                }
            }
        }

        let mut roots = BTreeSet::new();
        for edge in output.graph.edge_indices() {
            let Some((source, target)) = output.graph.edge_endpoints(edge) else {
                continue;
            };
            match (&output.graph[source], &output.graph[target]) {
                (ResolutionGraphNode::Root, ResolutionGraphNode::Dist(target)) => {
                    roots.insert(target.name.to_string());
                }
                (ResolutionGraphNode::Dist(source), ResolutionGraphNode::Dist(target))
                    if source.name != target.name =>
                {
                    let source_name = source.name.to_string();
                    if let Some((_, dependencies)) = packages.get_mut(&source_name) {
                        dependencies.insert(target.name.to_string());
                    }
                }
                (ResolutionGraphNode::Root, ResolutionGraphNode::Root)
                | (ResolutionGraphNode::Dist(_), ResolutionGraphNode::Root)
                | (ResolutionGraphNode::Dist(_), ResolutionGraphNode::Dist(_)) => {}
            }
        }

        let packages = packages
            .into_iter()
            .map(|(name, (version, dependencies))| {
                let (url, filename, sha256) = match files.remove(&name) {
                    Some((url, filename, sha256)) => (url, Some(filename), sha256),
                    None => (None, None, None),
                };
                PackagePin {
                    name,
                    version,
                    dependencies: dependencies.into_iter().collect(),
                    url,
                    filename,
                    sha256,
                }
            })
            .collect();
        Ok((packages, roots.into_iter().collect()))
    }

    #[unsafe(no_mangle)]
    pub extern "C" fn uv_resolve(input_json: *const c_char) -> *mut c_char {
        if input_json.is_null() {
            return output(ResolveOutput {
                ok: false,
                packages: None,
                roots: None,
                error: Some("input must not be null".to_string()),
            });
        }
        // SAFETY: The JavaScript wrapper allocates a NUL-terminated input string for this call.
        let input = unsafe { CStr::from_ptr(input_json) };
        let input = match input.to_str() {
            Ok(input) => input,
            Err(error) => {
                return output(ResolveOutput {
                    ok: false,
                    packages: None,
                    roots: None,
                    error: Some(error.to_string()),
                });
            }
        };
        let request = match serde_json::from_str::<ResolveInput>(input) {
            Ok(request) => request,
            Err(error) => {
                return output(ResolveOutput {
                    ok: false,
                    packages: None,
                    roots: None,
                    error: Some(error.to_string()),
                });
            }
        };

        match resolve(request) {
            Ok((packages, roots)) => output(ResolveOutput {
                ok: true,
                packages: Some(packages),
                roots: Some(roots),
                error: None,
            }),
            Err(error) => output(ResolveOutput {
                ok: false,
                packages: None,
                roots: None,
                error: Some(error),
            }),
        }
    }

    #[unsafe(no_mangle)]
    pub extern "C" fn uv_free(value: *mut c_char) {
        if value.is_null() {
            return;
        }
        // SAFETY: `value` is produced by `CString::into_raw` in `uv_resolve` and is freed once.
        let _ = unsafe { CString::from_raw(value) };
    }
}

#[cfg(target_os = "emscripten")]
fn main() {}
