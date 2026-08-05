from pathlib import Path


def test_lunar_tts_build_workflow_packages_license_and_service():
    workflow = Path(".github/workflows/build-lunar-tts-runtime.yml").read_text(encoding="utf-8")

    assert "Qwen3_TTS_Lunar.exe" in workflow
    assert "qwen3tts.dll" in workflow
    assert "libgcc_s_seh-1.dll" in workflow
    assert "libgomp-1.dll" in workflow
    assert "libstdc++-6.dll" in workflow
    assert "libwinpthread-1.dll" in workflow
    assert "LICENSE-Lunar-Astral-Agents.txt" in workflow
    assert "GGML_VULKAN=ON" in workflow
    assert "mingw-w64-ucrt-x86_64-vulkan-headers" in workflow
    assert "mingw-w64-ucrt-x86_64-shaderc" in workflow
    assert "mingw-w64-ucrt-x86_64-spirv-headers" in workflow
    assert "git add runtime/tts" in workflow
    assert 'cygpath "$GITHUB_WORKSPACE"' in workflow
    assert "Compress-Archive" not in workflow


def test_readme_discloses_lunar_non_commercial_tts_component():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Lunar Astral Agents" in readme
    assert "Non-Commercial License" in readme


def test_windows_build_uses_published_lunar_runtime():
    script = Path("scripts/build_windows.ps1").read_text(encoding="utf-8")

    assert 'runtime\\tts\\Qwen3_TTS_Lunar.exe' in script
    assert 'runtime\\tts\\qwen3-tts-cli.exe' not in script


def test_pyinstaller_spec_packages_lunar_tts_runtime():
    spec = Path("YUMENO.spec").read_text(encoding="utf-8")

    assert "('runtime/tts', 'runtime/tts')" in spec
