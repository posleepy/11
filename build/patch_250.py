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
config = 'add_repositories("archive-xl-local local-xmake-repo", {rootdir = os.scriptdir()})\n' + config
buildfile.write_text(config, encoding="utf-8")
print("Pinned TiltedCore transitive mimalloc dependency to 2.2.4.")


# Locate the failure inside RTTI callback registration, preserving the original code.
registrar = "lib/Red/TypeInfo/Registrar.hpp"
edit(registrar, [
    ('#pragma once\n', '''#pragma once
#include <fstream>
#include <filesystem>
namespace {
inline void TraceRTTI250(const char* msg) noexcept {
    try {
        std::ofstream out(std::filesystem::temp_directory_path() / "ArchiveXL-250-diag.log", std::ios::app);
        out << msg << std::endl;
    } catch (...) {}
}
}
'''),
    ('        QueuePendingRegisterCallbacks();\n        QueuePendingDescribeCallbacks();',
     '        TraceRTTI250("rtti.register.begin");\n        QueuePendingRegisterCallbacks();\n        TraceRTTI250("rtti.register.end");\n        QueuePendingDescribeCallbacks();\n        TraceRTTI250("rtti.describe.end");'),
    ('            CRTTISystem::Get()->AddRegisterCallback(&OnRegister);',
     '            TraceRTTI250("rtti.GetForRegister.begin");\n            auto* sys = CRTTISystem::Get();\n            TraceRTTI250("rtti.GetForRegister.end");\n            if (!sys) { TraceRTTI250("rtti.GetForRegister.NULL"); return; }\n            TraceRTTI250("rtti.AddRegister.begin");\n            sys->AddRegisterCallback(&OnRegister);\n            TraceRTTI250("rtti.AddRegister.end");'),
    ('            CRTTISystem::Get()->AddPostRegisterCallback(&OnDescribe);',
     '            TraceRTTI250("rtti.GetForDescribe.begin");\n            auto* sys = CRTTISystem::Get();\n            TraceRTTI250("rtti.GetForDescribe.end");\n            if (!sys) { TraceRTTI250("rtti.GetForDescribe.NULL"); return; }\n            TraceRTTI250("rtti.AddDescribe.begin");\n            sys->AddPostRegisterCallback(&OnDescribe);\n            TraceRTTI250("rtti.AddDescribe.end");')
])


# Probe ALL ArchiveXL address hashes using RED4ext's non-fatal native resolver.
# Skip only the unresolved archive hook, so the rest of initialization can be diagnosed.
import re
address_text = (root / "src/Red/Addresses/Library.hpp").read_text(encoding="utf-8")
hashes = re.findall(r'^\s*constexpr uint32_t (\w+) = (\d+);', address_text, re.M)
if len(hashes) < 60:
    raise RuntimeError(f"Unexpected number of ArchiveXL address definitions: {len(hashes)}")
table = ",\n".join(f'        {{"{name}", {num}u}}' for name, num in hashes)
probe = r"""
#include <windows.h>
#include <fstream>
#include <filesystem>
namespace {
using ArchiveXL250Resolver = uintptr_t (*)(uint32_t);
struct ArchiveXL250Address { const char* name; uint32_t hash; };
void ArchiveXL250ArchiveTrace(const char* msg) noexcept {
    try {
        std::ofstream out(std::filesystem::temp_directory_path() / "ArchiveXL-250-diag.log", std::ios::app);
        out << msg << std::endl;
    } catch (...) {}
}
bool ArchiveXL250ProbeAddressLibrary() noexcept {
    const auto module = GetModuleHandleW(L"RED4ext.dll");
    if (!module) {
        ArchiveXL250ArchiveTrace("archive.RED4ext.dll.MISSING");
        return false;
    }
    const auto resolver = reinterpret_cast<ArchiveXL250Resolver>(
        GetProcAddress(module, "RED4ext_ResolveAddress"));
    if (!resolver) {
        ArchiveXL250ArchiveTrace("archive.RED4ext_ResolveAddress.MISSING");
        return false;
    }
    static constexpr ArchiveXL250Address entries[] = {
__ADDRESS_ENTRIES__
    };
    unsigned missing = 0;
    for (const auto& entry : entries) {
        if (!resolver(entry.hash)) {
            ++missing;
            try {
                std::ofstream out(std::filesystem::temp_directory_path() / "ArchiveXL-250-diag.log", std::ios::app);
                out << "address.MISSING " << entry.name << " hash=" << entry.hash << std::endl;
            } catch (...) {}
        }
    }
    try {
        std::ofstream out(std::filesystem::temp_directory_path() / "ArchiveXL-250-diag.log", std::ios::app);
        out << "address.SUMMARY total=" << (sizeof(entries)/sizeof(entries[0]))
            << " missing=" << missing << std::endl;
    } catch (...) {}
    return resolver(2885423437u) != 0;
}
}
""".replace("__ADDRESS_ENTRIES__", table)
edit("src/App/Archives/ArchiveService.cpp", [
    ('#include "Core/Facades/Runtime.hpp"\n',
     '#include "Core/Facades/Runtime.hpp"\n' + probe),
    ('void App::ArchiveService::OnBootstrap()\n{\n    HookAfter<Raw::ResourceDepot::InitializeArchives>(&ArchiveService::OnInitializeArchives).OrThrow();\n}',
     '''void App::ArchiveService::OnBootstrap()
{
    ArchiveXL250ArchiveTrace("archive.OnBootstrap.begin");
    if (!ArchiveXL250ProbeAddressLibrary()) {
        ArchiveXL250ArchiveTrace("archive.OnBootstrap.SKIPPED_UNRESOLVED_INITIALIZE_ARCHIVES");
        return;
    }
    HookAfter<Raw::ResourceDepot::InitializeArchives>(&ArchiveService::OnInitializeArchives).OrThrow();
    ArchiveXL250ArchiveTrace("archive.OnBootstrap.end");
}'''),
    ('void App::ArchiveService::OnShutdown()\n{\n    Unhook<Raw::ResourceDepot::InitializeArchives>();\n}',
     '''void App::ArchiveService::OnShutdown()
{
    if (IsHooked<Raw::ResourceDepot::InitializeArchives>())
        Unhook<Raw::ResourceDepot::InitializeArchives>();
}'''),
])
print(f"Added non-fatal address scan for {len(hashes)} ArchiveXL hashes and skip for known missing archive hook.")
