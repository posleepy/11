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


# The 2.50 RED4ext address list has changed. Enumerate unresolved symbols
# nonfatally through the exported resolver rather than calling RawFunc
# (which terminates the game with an address error dialog).
import re
library = (root / "src/Red/Addresses/Library.hpp").read_text(encoding="utf-8")
defs = re.findall(r'^\s*constexpr uint32_t (\w+) = (\d+);', library, re.M)
if len(defs) < 10:
    raise RuntimeError(f"Unexpected {plugin} address table: {len(defs)} hashes")
entry_lines = ',\n'.join(f'      {{"{name}", {hashval}u}}' for name, hashval in defs)
scanner = r"""
#include <windows.h>
namespace {
void __PLUGIN__250ProbeAddressHashes() noexcept {
    using Resolver = uintptr_t (*)(uint32_t);
    auto module = GetModuleHandleW(L"RED4ext.dll");
    auto resolver = module ? reinterpret_cast<Resolver>(
        GetProcAddress(module, "RED4ext_ResolveAddress")) : nullptr;
    try {
        std::ofstream out(std::filesystem::temp_directory_path() / "__PLUGIN__-250-diag.log", std::ios::app);
        if (!resolver) { out << "address.RESOLVER_NOT_AVAILABLE" << std::endl; return; }
        struct Entry { const char* name; uint32_t hash; };
        static constexpr Entry entries[] = {
__ENTRIES__
        };
        size_t missing = 0;
        for (const auto& item : entries) {
            if (resolver(item.hash) == 0) {
                ++missing;
                out << "address.MISSING " << item.name << " hash=" << item.hash << std::endl;
            }
        }
        out << "address.SUMMARY total=" << (sizeof(entries)/sizeof(entries[0]))
            << " missing=" << missing << std::endl;
    } catch (...) {}
}
}
""".replace("__PLUGIN__", plugin).replace("__ENTRIES__", entry_lines)
edit("src/App/Application.cpp", [
    ('void App::Application::OnStarting()\n{',
     scanner + '\nvoid App::Application::OnStarting()\n{'),
    ('    Trace250Framework("app.OnStarting.begin");',
     '    Trace250Framework("app.OnStarting.begin");\n    ' + plugin + '250ProbeAddressHashes();'),
])
# This particular hash is absent in CP2077 2.50 and belongs to the Codeware
# widget-spawning hook. Disable ONLY the incomplete service for a controlled
# boot test; without it, Codeware's UI features won't function.
if plugin == "Codeware":
    widget_guard = r"""
#include <windows.h>
#include <fstream>
#include <filesystem>
namespace {
bool s_250WidgetHooksActive = false;
bool Codeware250WidgetAddressAvailable(uint32_t hash) noexcept {
    auto module = GetModuleHandleW(L"RED4ext.dll");
    if (!module) return false;
    using ResolveFunc = uintptr_t (*)(uint32_t);
    auto resolve = reinterpret_cast<ResolveFunc>(GetProcAddress(module, "RED4ext_ResolveAddress"));
    return resolve && resolve(hash) != 0;
}
void Codeware250LogWidgetMissing(uint32_t hash) noexcept {
    try {
        std::ofstream out(std::filesystem::temp_directory_path() / "Codeware-250-diag.log", std::ios::app);
        out << "Codeware.widget.SKIPPED_MISSING_ADDRESS hash=" << hash << std::endl;
    } catch (...) {}
}
}
"""
    edit("src/App/UI/WidgetSpawningService.cpp", [
        ('#include "App/Callback/Events/InkWidgetSpawnEvent.hpp"\n',
         '#include "App/Callback/Events/InkWidgetSpawnEvent.hpp"\n' + widget_guard),
        ('void App::WidgetSpawningService::OnBootstrap()\n{\n',
         '''void App::WidgetSpawningService::OnBootstrap()
{
    static constexpr uint32_t required[] = {1158555307u, 506278179u, 118698863u, 1396063719u, 2698985195u};
    for (uint32_t hash : required) {
        if (!Codeware250WidgetAddressAvailable(hash)) {
            Codeware250LogWidgetMissing(hash);
            return;
        }
    }
    s_250WidgetHooksActive = true;
'''),
        ('void App::WidgetSpawningService::OnShutdown()\n{\n',
         '''void App::WidgetSpawningService::OnShutdown()
{
    if (!s_250WidgetHooksActive) return;
'''),
    ])
    print("Codeware: guarded unavailable UI widget-spawning hook (diagnostic only).")
print(f"{plugin}: added nonfatal scan of {len(defs)} addresses.")


# Earliest possible safe phase trace: before application constructor and before bootstrap.
# Avoid filesystem/iostream inside DllMain; Main is invoked outside loader lock.
entry_trace = r"""
#include <windows.h>
namespace {
void Framework250EntryTrace(const char* message) noexcept {
    wchar_t tempDir[MAX_PATH] = {};
    const DWORD n = GetTempPathW(MAX_PATH, tempDir);
    if (!n || n >= MAX_PATH) return;
    wchar_t path[MAX_PATH] = {};
    if (wcscpy_s(path, MAX_PATH, tempDir) != 0) return;
    if (wcscat_s(path, MAX_PATH, L"__PLUGIN__-250-entry.log") != 0) return;
    HANDLE f = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return;
    DWORD written = 0;
    WriteFile(f, message, static_cast<DWORD>(strlen(message)), &written, nullptr);
    WriteFile(f, "\r\n", 2, &written, nullptr);
    CloseHandle(f);
}
}
""".replace("__PLUGIN__", plugin)
edit("src/main.cpp", [
    ('#include "Core/Facades/Runtime.hpp"\n',
     '#include "Core/Facades/Runtime.hpp"\n#include <cstring>\n' + entry_trace),
    ('    switch (aReason)\n    {\n    case RED4ext::v1::EMainReason::Load:',
     '    Framework250EntryTrace("entry.Main.called");\n    switch (aReason)\n    {\n    case RED4ext::v1::EMainReason::Load:'),
    ('        g_app = Core::MakeUnique<App::Application>(aHandle, aSdk);',
     '        Framework250EntryTrace("entry.BeforeConstructor");\n        g_app = Core::MakeUnique<App::Application>(aHandle, aSdk);\n        Framework250EntryTrace("entry.AfterConstructor");'),
    ('        Framework250EntryTrace("entry.AfterConstructor");\n        g_app->Bootstrap();',
     '        Framework250EntryTrace("entry.AfterConstructor");\n        Framework250EntryTrace("entry.BeforeBootstrap");\n        g_app->Bootstrap();\n        Framework250EntryTrace("entry.AfterBootstrap");'),
    ('RED4EXT_C_EXPORT void RED4EXT_CALL Query(RED4ext::v1::PluginInfo* aInfo)\n{',
     'RED4EXT_C_EXPORT void RED4EXT_CALL Query(RED4ext::v1::PluginInfo* aInfo)\n{\n    Framework250EntryTrace("entry.Query.called");'),
    ('RED4EXT_C_EXPORT uint32_t RED4EXT_CALL Supports()\n{',
     'RED4EXT_C_EXPORT uint32_t RED4EXT_CALL Supports()\n{\n    Framework250EntryTrace("entry.Supports.called");')
])
print(f"{plugin}: added entry trace before plugin constructor.")


# The CP2077 2.50 minidump exposes a changed ScriptBundle layout. The
# class-array pointer is stored at bundle+0xD8 (valid heap allocation with
# class pointers), while Codeware 1.20.6 expects +0xD0 (null). Shift the
# script-definition array fields from +0x90 by eight bytes. This is a
# narrowly scoped experimental fix, verified against the 2.50 crash dump.
if plugin == "Codeware":
    edit("src/Red/ScriptBundle.hpp", [
        ('    DynArray<ScriptFileInfo*> files;                     // 90',
         '    uint64_t unk90_250;                                   // 90 - extra 2.50 bundle field\n'
         '    DynArray<ScriptFileInfo*> files;                     // 98'),
        ('    DynArray<ScriptFunction*> globals;                   // A0',
         '    DynArray<ScriptFunction*> globals;                   // A8'),
        ('    DynArray<ScriptEnum*> enums;                         // B0',
         '    DynArray<ScriptEnum*> enums;                         // B8'),
        ('    DynArray<ScriptBitfield*> bitfields;                 // C0',
         '    DynArray<ScriptBitfield*> bitfields;                 // C8'),
        ('    DynArray<ScriptClass*> classes;                      // D0',
         '    DynArray<ScriptClass*> classes;                      // D8'),
        ('    DynArray<ScriptType*> types;                         // E0',
         '    DynArray<ScriptType*> types;                         // E8'),
        ('    DynArray<CString> strings;                           // F0',
         '    DynArray<CString> strings;                           // F8'),
        ('    HashMap<CName, void*> unk100;                        // 100',
         '    HashMap<CName, void*> unk100;                        // 108'),
        ('    SharedSpinLock typesLock;                            // 130',
         '    SharedSpinLock typesLock;                            // 138'),
        ('    CRITICAL_SECTION unk138;                             // 138',
         '    CRITICAL_SECTION unk138;                             // 140'),
        ('RED4EXT_ASSERT_SIZE(ScriptBundle, 0x160);',
         'RED4EXT_ASSERT_SIZE(ScriptBundle, 0x168);')
    ])
    edit("src/App/Scripting/ScriptingService.cpp", [
        ('void App::ScriptingService::OnValidateScripts(void* aValidator, Red::ScriptBundle* aBundle, void* aReport)\n{',
         '''void App::ScriptingService::OnValidateScripts(void* aValidator, Red::ScriptBundle* aBundle, void* aReport)
{
    if (!aBundle)
    {
        LogError("Codeware 2.50: ScriptBundle is null, skipping validation compatibility hook");
        return;
    }
    if (aBundle->classes.Size() && !aBundle->classes.Data())
    {
        LogError("Codeware 2.50: ScriptBundle classes header is invalid, skipping validation compatibility hook");
        return;
    }''')
    ])
    print("Codeware: ScriptBundle class array corrected to +0xD8, guarded malformed arrays.")
