use std::env;
use std::path::PathBuf;

fn main() {
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("emscripten") {
        return;
    }

    let manifest_dir = match env::var("CARGO_MANIFEST_DIR") {
        Ok(value) => value,
        Err(_) => return,
    };
    let fetch_library = PathBuf::from(manifest_dir).join("src/fetch.js");
    let Some(fetch_library) = fetch_library.to_str() else {
        return;
    };

    println!("cargo::rerun-if-changed=src/fetch.js");
    for argument in [
        "-sJSPI",
        &format!("--js-library={fetch_library}"),
        "-sJSPI_EXPORTS=uv_resolve,uv_free",
        "-sMODULARIZE",
        "-sEXPORT_ES6",
        "-sEXPORT_NAME=createUvResolver",
        "-sENVIRONMENT=web,node",
        "-sALLOW_MEMORY_GROWTH=1",
        "-sSTACK_SIZE=8MB",
        "-sEXPORTED_FUNCTIONS=_main,_uv_resolve,_uv_free,_malloc,_free",
        "-sEXPORTED_RUNTIME_METHODS=UTF8ToString,stringToNewUTF8",
        "-sINCOMING_MODULE_JS_API=printErr",
        "-sEXIT_RUNTIME=0",
    ] {
        println!("cargo::rustc-link-arg-bins={argument}");
    }
}
