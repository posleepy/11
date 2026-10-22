#!/usr/bin/env python3
"""Instrument ArchiveXL v1.27.5 startup. Diagnostic only; not a 2.50 compatibility fix."""
from pathlib import Path
import sys

root = Path(sys.argv[1])
def edit(name, transformations):
    path = root / name
    contents = path.read_text(encoding="utf-8")
    for before, after in transformations:
        if contents.count(before) != 1:
            raise RuntimeError(f"Expected unique source anchor in {name}: {before[:80]!r}, found {contents.count(before)}")
        contents = contents.replace(before, after, 1)
    path.write_text(contents, encoding="utf-8")

trace = """
#include <windows.h>
#include <fstream>
#include <filesystem>
namespace {
void Trace250(const char* message) noexcept {
    try {
        std::ofstream out(std::filesystem::temp_directory_path() / "ArchiveXL-250-diag.log", std::ios::app);
        out << message << std::endl;
    } catch (...) {}
}
}
"""
app = "src/App/Application.cpp"
edit(app, [
    ('#include "Support/Spdlog/SpdlogProvider.hpp"\n', '#include "Support/Spdlog/SpdlogProvider.hpp"\n' + trace),
    ('{\n    Register<Core::RuntimeProvider>(aHandle)', '{\n    Trace250("app.constructor.begin");\n    Register<Core::RuntimeProvider>(aHandle)'),
    ('    Register<Support::RedLibProvider>();', '    Trace250("app.before.RedLibProvider");\n    Register<Support::RedLibProvider>();\n    Trace250("app.after.RedLibProvider");'),
    ('    Register<App::WorldWidgetLimitPatch>();', '    Register<App::WorldWidgetLimitPatch>();\n    Trace250("app.constructor.end");'),
])
core = "lib/Core/Foundation/Application.cpp"
core_trace = """
#include <fstream>
#include <filesystem>
namespace {
void TraceCore250(const char* phase, size_t index) noexcept {
    try {
        std::ofstream out(std::filesystem::temp_directory_path() / "ArchiveXL-250-diag.log", std::ios::app);
        out << "core." << phase << ":" << index << std::endl;
    } catch (...) {}
}
}
"""
edit(core, [
    ('#include "Application.hpp"\n', '#include "Application.hpp"\n' + core_trace),
    ('    OnStarting();\n\n    for (const auto& feature : GetRegistered())\n    {\n        feature->OnBootstrap();\n    }',
     '    TraceCore250("OnStarting.begin", 0);\n    OnStarting();\n    TraceCore250("OnStarting.end", 0);\n\n    size_t i = 0;\n    for (const auto& feature : GetRegistered())\n    {\n        TraceCore250("OnBootstrap.begin", i);\n        feature->OnBootstrap();\n        TraceCore250("OnBootstrap.end", i);\n        ++i;\n    }'),
])
red = "lib/Support/RedLib/RedLibProvider.cpp"
red_trace = """
#include <fstream>
#include <filesystem>
namespace {
void TraceRed250(const char* phase) noexcept {
    try {
        std::ofstream out(std::filesystem::temp_directory_path() / "ArchiveXL-250-diag.log", std::ios::app);
        out << "RedLib." << phase << std::endl;
    } catch (...) {}
}
}
"""
edit(red, [
    ('#include "Red/TypeInfo/Registrar.hpp"\n', '#include "Red/TypeInfo/Registrar.hpp"\n' + red_trace),
    ('    Red::TypeInfoRegistrar::RegisterDiscovered();',
     '    TraceRed250("RegisterDiscovered.begin");\n    Red::TypeInfoRegistrar::RegisterDiscovered();\n    TraceRed250("RegisterDiscovered.end");')
])
print("Instrumented three source files successfully.")


# Xmake currently chooses mimalloc v3 for a transitive TiltedCore dependency.
# TiltedCore v0.2.9 still references mi_malloc_size; pin compatible v2 and link explicitly.
buildfile = root / "xmake.lua"
original = buildfile.read_text(encoding="utf-8")
require_anchor = 'add_requires("hopscotch-map", "minhook", "spdlog", "tiltedcore", "yaml-cpp")'
packages_anchor = '    add_packages("hopscotch-map", "minhook", "spdlog", "tiltedcore", "yaml-cpp")'
if original.count(require_anchor) != 1 or original.count(packages_anchor) != 1:
    raise RuntimeError("Unexpected xmake.lua structure; refusing automatic dependency edit")
original = original.replace(require_anchor,
    'add_requires("mimalloc v2.2.4", {configs = {rltgenrandom = true}})\n' + require_anchor, 1)
original = original.replace(packages_anchor,
    packages_anchor.replace('"yaml-cpp")', '"yaml-cpp", "mimalloc")'), 1)
buildfile.write_text(original, encoding="utf-8")
print("Pinned and linked mimalloc v2.2.4 for TiltedCore compatibility.")


# Override the *transitive* TiltedCore dependency too, so xmake does not
# secretly bring in mimalloc 3.x alongside our explicit mimalloc 2.x.
recipe_path = root / "local-xmake-repo" / "packages" / "t" / "tiltedcore" / "xmake.lua"
recipe_path.parent.mkdir(parents=True, exist_ok=True)
recipe_path.write_text(r'''package("tiltedcore")
    set_homepage("https://github.com/tiltedphoques/TiltedCore")
    set_description("Core library from Tilted Phoques (mimalloc 2.2.4 override)")
    add_urls("https://github.com/tiltedphoques/TiltedCore/archive/$(version).zip")
    add_urls("https://github.com/tiltedphoques/TiltedCore.git")
    add_versions("v0.2.9", "aa8dd5aee40c53fd57da91ef5193458b62f3dec420e78fac9dc8293a987b9758")
    add_configs("shared", {description = "Build shared library.", default = false, type = "boolean", readonly = true})
    add_deps("mimalloc v2.2.4", {configs = {rltgenrandom = true}})
    on_install("windows", "mingw", "linux", "cross", "android", "macosx|!arm*", function (package)
        import("package.tools.xmake").install(package)
    end)
    on_test(function (package)
        assert(package:check_cxxsnippets({test = [[
            void test(int args, char** argv) {
                TiltedPhoques::Outcome<int, float> outcome;
            }
        ]]}, {includes = {"TiltedCore/Outcome.hpp"}}))
    end)
''', encoding="utf-8")
config = buildfile.read_text(encoding="utf-8")
config = 'add_repositories("archive-xl-local local-xmake-repo", {rootdir = os.scriptdir()})\\n' + config
buildfile.write_text(config.replace('\\\\n', '\\n'), encoding="utf-8")
print("Pinned TiltedCore transitive mimalloc dependency to 2.2.4.")
