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
