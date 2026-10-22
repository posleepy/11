#!/usr/bin/env python3
"""Build instrumentation for Codeware/TweakXL on Cyberpunk 2077 2.50.
No game function addresses are guessed. NOT a working compatibility fix.
"""
from pathlib import Path
import sys
if len(sys.argv) != 3 or sys.argv[2] not in ("Codeware", "TweakXL"):
    raise SystemExit("usage: patch_framework_250.py <source-dir> <Codeware|TweakXL>")
root = Path(sys.argv[1])
plugin = sys.argv[2]
def edit(path, edits):
    dest = root / path
    s = dest.read_text(encoding="utf-8")
    for source, target in edits:
        n = s.count(source)
        if n != 1:
            raise RuntimeError(f"{plugin}: {path}: anchor found {n} times: {source[:75]!r}")
        s = s.replace(source, target, 1)
    dest.write_text(s, encoding="utf-8")
trace = r'''
#include <fstream>
#include <filesystem>
namespace {
void Trace250Framework(const char* phase, size_t i = 0) noexcept {
    try {
        std::ofstream log(std::filesystem::temp_directory_path() / "__MODNAME__-250-diag.log", std::ios::app);
        log << phase << ":" << i << std::endl;
    } catch (...) {}
}
}
'''.replace("__MODNAME__", plugin)
edit("src/App/Application.cpp", [
    ('#include "Support/Spdlog/SpdlogProvider.hpp"\n', '#include "Support/Spdlog/SpdlogProvider.hpp"\n' + trace),
    ('{\n    Register<', '{\n    Trace250Framework("app.constructor.begin");\n    Register<'),
    ('    Register<Support::RedLibProvider>();',
     '    Trace250Framework("app.RegisterRedLib.begin");\n    Register<Support::RedLibProvider>();\n    Trace250Framework("app.RegisterRedLib.end");'),
    ('void App::Application::OnStarting()\n{',
     'void App::Application::OnStarting()\n{\n    Trace250Framework("app.OnStarting.begin");')
])
edit("lib/Core/Foundation/Application.cpp", [
    ('#include "Application.hpp"\n', '#include "Application.hpp"\n' + trace),
    ('    OnStarting();\n\n    for (const auto& feature : GetRegistered())\n    {\n        feature->OnBootstrap();\n    }',
     '    Trace250Framework("core.OnStarting.begin");\n    OnStarting();\n    Trace250Framework("core.OnStarting.end");\n    size_t i = 0;\n    for (const auto& feature : GetRegistered())\n    {\n        Trace250Framework("core.OnBootstrap.begin", i);\n        feature->OnBootstrap();\n        Trace250Framework("core.OnBootstrap.end", i);\n        ++i;\n    }')
])
edit("lib/Support/RedLib/RedLibProvider.cpp", [
    ('#include "Red/TypeInfo/Registrar.hpp"\n', '#include "Red/TypeInfo/Registrar.hpp"\n' + trace),
    ('    Red::TypeInfoRegistrar::RegisterDiscovered();',
     '    Trace250Framework("redlib.RegisterDiscovered.begin");\n    Red::TypeInfoRegistrar::RegisterDiscovered();\n    Trace250Framework("redlib.RegisterDiscovered.end");')
])
# Override xmake's transitive mimalloc 3.x dependency, which previously
# caused linking failures with a precompiled TiltedCore 0.2.9.
recipe = root / "local-xmake-repo" / "packages" / "t" / "tiltedcore" / "xmake.lua"
recipe.parent.mkdir(parents=True, exist_ok=True)
recipe.write_text(r'''package("tiltedcore")
    set_homepage("https://github.com/tiltedphoques/TiltedCore")
    set_description("TiltedCore with mimalloc 2.2.4 dependency")
    add_urls("https://github.com/tiltedphoques/TiltedCore/archive/$(version).zip")
    add_urls("https://github.com/tiltedphoques/TiltedCore.git")
    add_versions("v0.2.9", "aa8dd5aee40c53fd57da91ef5193458b62f3dec420e78fac9dc8293a987b9758")
    add_configs("shared", {description = "Build shared library.", default = false, type = "boolean", readonly = true})
    add_deps("mimalloc v2.2.4", {configs = {rltgenrandom = true}})
    on_install("windows", "mingw", "linux", "cross", "android", "macosx|!arm*", function (package)
        import("package.tools.xmake").install(package)
    end)
''', encoding="utf-8")
buildfile = root / "xmake.lua"
s = buildfile.read_text(encoding="utf-8")
required = 'add_requires("hopscotch-map", "minhook", "spdlog", "tiltedcore"'
packages = '    add_packages("hopscotch-map", "minhook", "spdlog", "tiltedcore"'
if s.count(required) != 1 or s.count(packages) != 1:
    raise RuntimeError("Unexpected xmake.lua structure")
s = s.replace(required, 'add_requires("mimalloc v2.2.4", {configs = {rltgenrandom = true}})\n' + required, 1)
line = next(line for line in s.splitlines() if line.startswith(packages))
s = s.replace(line, line[:-1] + ', "mimalloc")', 1)
s = 'add_repositories("framework250-local local-xmake-repo", {rootdir = os.scriptdir()})\n' + s
buildfile.write_text(s, encoding="utf-8")
print(f"{plugin}: diagnostic instrumentation and 2.50 dependencies applied.")
