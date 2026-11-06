"""Architectural fitness tests enforcing Hexagonal Architecture boundary rules."""

import ast
from pathlib import Path

from lookout.adapters.memory import (
    InMemoryEventRepository,
    MockAudioClassifierAdapter,
    MockFaceRecognitionAdapter,
    MockNotificationAdapter,
    MockVideoRecorderAdapter,
    MockVisionInferenceAdapter,
)
from lookout.domain.service import EventProcessingService
from lookout.ports.egress import (
    AudioClassifierPort,
    EventRepositoryPort,
    FaceRecognitionPort,
    NotificationPort,
    VideoRecorderPort,
    VisionInferencePort,
)
from lookout.ports.ingress import EventIngressPort

LOOKOUT_ROOT = Path(__file__).parent.parent / "src" / "lookout"


def _extract_imported_modules(file_path: Path) -> list[str]:
    """Parse python AST and extract all top-level imported module paths."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def test_domain_has_no_adapter_or_external_imports() -> None:
    """TR-11: Verify lookout.domain has zero imports from adapters, config, or external I/O."""
    domain_dir = LOOKOUT_ROOT / "domain"
    forbidden_prefixes = (
        "lookout.adapters",
        "lookout.config",
        "requests",
        "httpx",
        "aiohttp",
        "urllib.request",
        "sqlite3",
        "socket",
        "subprocess",
    )

    forbidden_imports: list[str] = []
    for py_file in domain_dir.glob("*.py"):
        imported_modules = _extract_imported_modules(py_file)
        for mod in imported_modules:
            if any(mod == prefix or mod.startswith(f"{prefix}.") for prefix in forbidden_prefixes):
                forbidden_imports.append(f"{py_file.name} -> {mod}")

    assert forbidden_imports == []


def test_ports_are_runtime_checkable() -> None:
    """TR-12: Verify all defined ports are runtime_checkable protocols."""
    adapter = InMemoryEventRepository()
    PortProtocol = EventRepositoryPort
    assert isinstance(adapter, PortProtocol)

    # Verify all mock adapters implement their respective egress and ingress ports
    assert isinstance(MockAudioClassifierAdapter(), AudioClassifierPort)
    assert isinstance(MockFaceRecognitionAdapter(), FaceRecognitionPort)
    assert isinstance(MockNotificationAdapter(), NotificationPort)
    assert isinstance(MockVideoRecorderAdapter(), VideoRecorderPort)
    assert isinstance(MockVisionInferenceAdapter(), VisionInferencePort)
    assert isinstance(
        EventProcessingService(repository=InMemoryEventRepository()),
        EventIngressPort,
    )


def test_ports_have_no_adapter_imports() -> None:
    """TR-13: Verify lookout.ports has zero dependencies on adapters."""
    ports_dir = LOOKOUT_ROOT / "ports"
    forbidden_imports: list[str] = []

    for py_file in ports_dir.glob("*.py"):
        imported_modules = _extract_imported_modules(py_file)
        for mod in imported_modules:
            if mod == "lookout.adapters" or mod.startswith("lookout.adapters."):
                forbidden_imports.append(f"{py_file.name} -> {mod}")

    assert forbidden_imports == []


def test_adapters_do_not_cross_import_siblings() -> None:
    """TR-14: Verify adapter implementations do not cross-import sibling adapters."""
    adapters_memory_dir = LOOKOUT_ROOT / "adapters" / "memory"
    adapter_module_names = {
        py_file.stem for py_file in adapters_memory_dir.glob("*.py") if py_file.stem != "__init__"
    }

    forbidden_cross_imports: list[str] = []
    for py_file in adapters_memory_dir.glob("*.py"):
        if py_file.stem == "__init__":
            continue
        current_module = py_file.stem
        sibling_modules = adapter_module_names - {current_module}

        imported_modules = _extract_imported_modules(py_file)
        for mod in imported_modules:
            for sibling in sibling_modules:
                if (
                    mod == f"lookout.adapters.memory.{sibling}"
                    or mod.endswith(f".{sibling}")
                    or mod == sibling
                ):
                    forbidden_cross_imports.append(f"{py_file.name} -> {mod}")

    assert forbidden_cross_imports == []


def test_ai_ports_are_runtime_checkable() -> None:
    """TR-1: Verify AI ports (vision, audio, face) are runtime_checkable protocols."""
    assert isinstance(MockVisionInferenceAdapter(), VisionInferencePort)
    assert isinstance(MockAudioClassifierAdapter(), AudioClassifierPort)
    assert isinstance(MockFaceRecognitionAdapter(), FaceRecognitionPort)


def test_ai_adapters_do_not_cross_import_siblings() -> None:
    """TR-2: Verify AI adapters do not cross-import sibling adapters."""
    adapters_dir = LOOKOUT_ROOT / "adapters"
    adapter_module_names = {
        py_file.stem for py_file in adapters_dir.glob("*.py") if py_file.stem != "__init__"
    }

    forbidden_imports: list[str] = []
    for py_file in adapters_dir.glob("*.py"):
        if py_file.stem == "__init__":
            continue
        current_module = py_file.stem
        sibling_modules = adapter_module_names - {current_module}

        imported_modules = _extract_imported_modules(py_file)
        for mod in imported_modules:
            for sibling in sibling_modules:
                if (
                    mod == f"lookout.adapters.{sibling}"
                    or mod.endswith(f".{sibling}")
                    or mod == sibling
                ):
                    forbidden_imports.append(f"{py_file.name} -> {mod}")

    assert forbidden_imports == []


def test_phase3_adapters_implement_egress_ports() -> None:
    """TR-33: Verify Phase 3 concrete adapters implement egress ports."""
    from lookout.adapters import (
        CascadeVisionAdapter,
        FaceRecognitionAdapter,
        YamnetAudioClassifierAdapter,
    )

    assert isinstance(CascadeVisionAdapter(), VisionInferencePort)
    assert isinstance(FaceRecognitionAdapter(), FaceRecognitionPort)
    assert isinstance(YamnetAudioClassifierAdapter(), AudioClassifierPort)
