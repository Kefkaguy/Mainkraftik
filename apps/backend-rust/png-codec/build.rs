fn main() {
    let directory = "vendor/zlib-1.3";
    let mut build = cc::Build::new();
    build
        .include(directory)
        .define("Z_PREFIX", None)
        .define("Z_SOLO", None)
        .warnings(false);
    for name in ["adler32", "crc32", "deflate", "trees", "zutil"] {
        build.file(format!("{directory}/{name}.c"));
    }
    build
        .file("src/classic.c")
        .compile("mainkraft_zlib13_deflate");
    println!("cargo:rerun-if-changed={directory}");
    println!("cargo:rerun-if-changed=src/classic.c");
}
