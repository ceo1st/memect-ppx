import contextlib
import importlib.util
import io
import json
import os
import platform
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from memect.base import images, pdfs
from memect.base.api import ApiError
from memect.base.config import _load_settings
from memect.pdf.base import Backend, OCRMode, ParseMode, TableMode, TreeBackend


_OSC_RE = re.compile(r"\x1b\](?:.|\n)*?(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_RE = re.compile(r"\x1b[@-Z\\-_]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


CheckStatus = Literal["ok", "warning", "error", "skipped"]
CheckKind = Literal["config", "environment", "input", "network", "code", "unknown"]


class ConfigPatch(BaseModel):
    path: str
    value: Any
    reason: str | None = None


class DoctorCheck(BaseModel):
    id: str
    status: CheckStatus
    kind: CheckKind
    message: str
    details: Mapping[str, Any] = Field(default_factory=dict)
    suggested_patches: list[ConfigPatch] = Field(default_factory=list)


class DoctorReport(BaseModel):
    ok: bool = True
    checks: list[DoctorCheck] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)

    def add(
        self,
        id: str,
        status: CheckStatus,
        kind: CheckKind,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        suggested_patches: Sequence[ConfigPatch] | None = None,
    ) -> None:
        self.checks.append(
            DoctorCheck(
                id=id,
                status=status,
                kind=kind,
                message=message,
                details=details or {},
                suggested_patches=list(suggested_patches or ()),
            )
        )
        if status == "error":
            self.ok = False


class DoctorArgs(BaseModel):
    file: Path | None = None
    out_dir: Path | None = None
    as_doc: bool = False
    pages: str | None = None
    backend: Backend | str | None = None
    llm: str | None = None
    mode: ParseMode | str | None = None
    ocr: OCRMode | str | None = None
    table: TableMode | str | None = None
    formula: str | None = None
    tree: TreeBackend | str | None = None
    params_text: str | None = None
    params_file: Path | None = None
    conf_dir: Path = Path("./conf")
    custom_settings: dict[str, Any] = Field(default_factory=dict)
    cpu: str | None = None
    cuda: str | None = None
    llm_timeout: float = 5
    check_network: bool = True


class LLMInfo(BaseModel):
    name: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class _FileInfo(BaseModel):
    exists: bool = False
    type: Literal["pdf", "image", "image_dir", "unsupported", "missing"] = "missing"
    page_count: int | None = None
    image_count: int | None = None
    size: tuple[int, int] | None = None


class Doctor:
    def __init__(self, args: DoctorArgs | Mapping[str, Any] | None = None):
        self.args = DoctorArgs.model_validate(args or {})
        self.report = DoctorReport()
        self._settings: Mapping[str, Any] | None = None
        self._llm_info: LLMInfo | None = None
        self._effective_backend: Backend | None = None

    def run(self) -> DoctorReport:
        self._apply_device()
        self._check_runtime()
        self._check_imports()
        self._prepare_parse_options()
        self._load_config()
        self._check_file()
        self._check_output_dir()
        if self._settings is not None:
            self._check_config_shape()
            self._check_backend()
            self._check_model_references()
            self._check_model_paths()
            self._check_providers()
            self._check_llm_configs()
        return self.report

    def _apply_device(self) -> None:
        if self.args.cpu:
            os.environ["PPX_CPU"] = self.args.cpu
        if self.args.cuda:
            os.environ["CUDA_VISIBLE_DEVICES"] = self.args.cuda

    def _check_runtime(self) -> None:
        ok = sys.version_info >= (3, 12)
        self.report.add(
            "runtime.python",
            "ok" if ok else "error",
            "environment",
            "Python runtime is supported" if ok else "Python >= 3.12 is required",
            details={
                "version": sys.version.split()[0],
                "executable": sys.executable,
                "platform": platform.platform(),
            },
        )

    def _check_imports(self) -> None:
        modules = {
            "filetype": "filetype",
            "httpx": "httpx",
            "openai": "openai",
            "PIL": "pillow",
            "pydantic": "pydantic",
            "pymupdf": "pymupdf",
            "rich": "rich",
            "typer": "typer",
        }
        if sys.platform != "darwin":
            modules["onnxruntime"] = "onnxruntime"

        missing = [
            package
            for module, package in modules.items()
            if importlib.util.find_spec(module) is None
        ]
        self.report.add(
            "runtime.imports",
            "error" if missing else "ok",
            "environment",
            "Required modules are available"
            if not missing
            else "Required modules are missing",
            details={"missing": missing},
        )

    def _prepare_parse_options(self) -> None:
        args = self.args
        custom = dict(args.custom_settings)

        params_error: str | None = None
        if args.params_file and not args.params_file.is_file():
            params_error = f"params file does not exist: {args.params_file}"
        elif args.params_text:
            try:
                json.loads(args.params_text)
            except json.JSONDecodeError as e:
                params_error = str(e)
        if params_error:
            self.report.add(
                "parse.params",
                "error",
                "config",
                "Parse params are invalid",
                details={"error": params_error},
            )

        if args.llm:
            info = self._parse_llm(args.llm)
            self._llm_info = info
            if info.name:
                self._effective_backend = Backend(info.name)
                for key, value in info.raw.items():
                    if key != "name":
                        custom[f"pdf_parser.{info.name}.model.{key}"] = value

        if args.backend is not None:
            backend = self._to_enum(Backend, args.backend)
            if backend:
                self._effective_backend = backend
            else:
                self.report.add(
                    "parse.backend",
                    "error",
                    "config",
                    "Backend is invalid",
                    details={"backend": str(args.backend)},
                )

        if args.formula:
            self._apply_formula_settings(custom, args.formula)

        for enum_cls, value, check_id in (
            (ParseMode, args.mode, "parse.mode"),
            (OCRMode, args.ocr, "parse.ocr"),
            (TableMode, args.table, "parse.table"),
            (TreeBackend, args.tree, "parse.tree"),
        ):
            if value is not None and self._to_enum(enum_cls, value) is None:
                self.report.add(
                    check_id,
                    "error",
                    "config",
                    "Parse option is invalid",
                    details={"value": str(value)},
                )

        if args.pages:
            try:
                self._parse_pages(args.pages)
            except ValueError as e:
                self.report.add(
                    "parse.pages",
                    "error",
                    "config",
                    "Page range is invalid",
                    details={"error": str(e), "pages": args.pages},
                )

        self.args.custom_settings = custom
        self.report.summary["custom_settings"] = self._redact(custom)

    def _load_config(self) -> None:
        import memect.conf

        default_conf_dir = Path(memect.conf.__file__).parent
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                settings = _load_settings(
                    default_conf_dir / "settings.default.py",
                    custom_settings=self.args.custom_settings,
                    custom_dir=self.args.conf_dir.resolve(),
                )
            self._settings = settings
            self.report.add(
                "config.load",
                "ok",
                "config",
                "Configuration loaded",
                details={
                    "conf_dir": str(self.args.conf_dir.resolve()),
                    "captured_output": self._clean_capture(stdout.getvalue()),
                    "captured_error": self._clean_capture(stderr.getvalue()),
                },
            )
        except Exception as e:
            self.report.add(
                "config.load",
                "error",
                "config",
                "Configuration failed to load",
                details={
                    "conf_dir": str(self.args.conf_dir.resolve()),
                    "error_type": type(e).__name__,
                    "error": str(e),
                    "captured_output": self._clean_capture(stdout.getvalue()),
                    "captured_error": self._clean_capture(stderr.getvalue()),
                },
            )

    def _check_file(self) -> None:
        if self.args.file is None:
            self.report.add(
                "input.file",
                "skipped",
                "input",
                "No input file was provided",
            )
            return

        info = self._inspect_file(self.args.file)
        status: CheckStatus = "ok"
        message = "Input file is supported"
        if not info.exists:
            status = "error"
            message = "Input file does not exist"
        elif info.type == "unsupported":
            status = "error"
            message = "Input file type is unsupported"

        if self.args.pages and info.page_count is not None:
            pages = self._parse_pages(self.args.pages)
            too_large = [p for p in pages if p > info.page_count]
            if too_large:
                status = "error"
                message = "Page range exceeds page count"

        self.report.add(
            "input.file",
            status,
            "input",
            message,
            details=info.model_dump(mode="json") | {"file": str(self.args.file)},
        )

    def _inspect_file(self, file: Path) -> _FileInfo:
        info = _FileInfo(exists=file.exists())
        if not file.exists():
            return info
        if file.is_dir():
            images_found = [
                p
                for p in file.iterdir()
                if p.is_file()
                and p.name[:1] != "."
                and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp")
            ]
            info.type = "image_dir"
            info.image_count = len(images_found)
            if self.args.as_doc:
                bad_names = [p.name for p in images_found if not p.stem.isdigit()]
                if bad_names:
                    self.report.add(
                        "input.image_dir.names",
                        "error",
                        "input",
                        "Image directory pages must use numeric filenames when --as-doc is set",
                        details={"bad_names": bad_names[:20]},
                    )
            if not images_found:
                info.type = "unsupported"
            return info

        file_type = self._guess_file_type(file)
        info.type = file_type
        if file_type == "pdf":
            try:
                info.page_count = pdfs.page_count(file)
            except ApiError as e:
                self.report.add(
                    "input.pdf.open",
                    "error",
                    "input",
                    "PDF cannot be opened",
                    details={"error": e.message},
                )
        elif file_type == "image":
            try:
                info.size = images.size(file)
            except Exception as e:
                self.report.add(
                    "input.image.open",
                    "error",
                    "input",
                    "Image cannot be opened",
                    details={"error_type": type(e).__name__, "error": str(e)},
                )
        return info

    def _guess_file_type(
        self, file: Path
    ) -> Literal["pdf", "image", "image_dir", "unsupported", "missing"]:
        try:
            import filetype

            kind = filetype.guess(file)
            if kind:
                if kind.extension == "pdf":
                    return "pdf"
                if kind.extension in ("png", "jpeg", "jpg", "webp", "bmp"):
                    return "image"
        except Exception:
            pass

        suffix = file.suffix.lower()
        if suffix == ".pdf":
            return "pdf"
        if suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            return "image"
        return "unsupported"

    def _check_output_dir(self) -> None:
        out_dir = self.args.out_dir
        if out_dir is None:
            if self.args.file is None:
                out_dir = Path("./out")
            else:
                out_dir = Path(f"{self.args.file}.out")

        parent = self._nearest_existing_parent(out_dir)
        try:
            with tempfile.NamedTemporaryFile(prefix=".ppx-doctor-", dir=parent):
                pass
            self.report.add(
                "output.dir",
                "ok",
                "environment",
                "Output location is writable",
                details={"out_dir": str(out_dir), "checked_dir": str(parent)},
            )
        except Exception as e:
            self.report.add(
                "output.dir",
                "error",
                "environment",
                "Output location is not writable",
                details={
                    "out_dir": str(out_dir),
                    "checked_dir": str(parent),
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )

    def _check_config_shape(self) -> None:
        assert self._settings is not None
        missing = [
            key
            for key in ("pdf_parser", "model_manager", "pdf_service")
            if key not in self._settings
        ]
        self.report.add(
            "config.shape",
            "error" if missing else "ok",
            "config",
            "Configuration has required top-level sections"
            if not missing
            else "Configuration is missing top-level sections",
            details={"missing": missing},
        )

    def _check_backend(self) -> None:
        backend = self._effective_backend or Backend.DEFAULT
        self.report.summary["backend"] = backend.value

        if backend == Backend.DEFAULT:
            self.report.add(
                "backend.default",
                "ok",
                "config",
                "Default parser backend is selected",
            )
            return

        cfg = self._get(f"pdf_parser.{backend.value}.model")
        if not isinstance(cfg, Mapping):
            self.report.add(
                "backend.llm.config",
                "error",
                "config",
                "LLM backend configuration is missing",
                details={"backend": backend.value},
            )
            return

        base_url = cfg.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            self.report.add(
                "backend.llm.base_url",
                "error",
                "config",
                "LLM backend base_url is missing",
                details={"backend": backend.value, "base_url": base_url},
            )
            return

        self.report.add(
            "backend.llm.config",
            "ok",
            "config",
            "LLM backend configuration is present",
            details={"backend": backend.value, "base_url": base_url},
        )
        if self.args.check_network:
            self._probe_llm(base_url, expected=backend.value, check_id="backend.llm.models")

    def _check_model_references(self) -> None:
        manager = self._get("model_manager")
        if not isinstance(manager, Mapping):
            return
        executors = manager.get("executors")
        models = manager.get("models")
        if not isinstance(executors, Mapping) or not isinstance(models, Mapping):
            return

        missing: list[dict[str, Any]] = []
        for name, executor in executors.items():
            if not isinstance(executor, Mapping):
                continue
            if executor.get("enable", True) is False:
                continue
            model = executor.get("model")
            if isinstance(model, str) and model not in models:
                missing.append({"executor": name, "model": model})

        self.report.add(
            "model.references",
            "error" if missing else "ok",
            "config",
            "Model executor references are valid"
            if not missing
            else "Model executor references missing models",
            details={"missing": missing},
        )

    def _check_model_paths(self) -> None:
        models = self._get("model_manager.models")
        if not isinstance(models, Mapping):
            return

        missing: list[dict[str, Any]] = []
        auto: list[dict[str, Any]] = []
        for model_name, cfg in models.items():
            if not isinstance(cfg, Mapping):
                continue
            kwargs = cfg.get("kwargs")
            if not isinstance(kwargs, Mapping):
                continue
            for key, value in kwargs.items():
                if not self._is_path_key(str(key)):
                    continue
                if value is None:
                    auto.append({"model": model_name, "key": key})
                elif isinstance(value, str) and not self._looks_like_url(value):
                    path = Path(value)
                    if not path.exists():
                        missing.append(
                            {"model": model_name, "key": key, "path": value}
                        )

        if missing:
            self.report.add(
                "model.paths",
                "error",
                "environment",
                "Configured model paths do not exist",
                details={"missing": missing},
            )
        elif auto:
            self.report.add(
                "model.paths",
                "warning",
                "environment",
                "Some model paths are unset and may require automatic download",
                details={"auto": auto},
            )
        else:
            self.report.add(
                "model.paths",
                "ok",
                "environment",
                "Configured model paths exist",
            )

    def _check_providers(self) -> None:
        models = self._get("model_manager.models")
        if not isinstance(models, Mapping):
            return

        required_onnx = False
        needs: dict[str, list[str]] = {
            "CUDAExecutionProvider": [],
            "CANNExecutionProvider": [],
            "DmlExecutionProvider": [],
        }
        openvino_models: list[str] = []

        for model_name, cfg in models.items():
            if not isinstance(cfg, Mapping):
                continue
            kwargs = cfg.get("kwargs")
            if not isinstance(kwargs, Mapping):
                continue
            engine_values = {
                str(v).lower()
                for k, v in kwargs.items()
                if str(k).endswith("engine") or str(k).endswith("engine_type")
            }
            if "onnxruntime" in engine_values:
                required_onnx = True
            if "openvino" in engine_values:
                openvino_models.append(str(model_name))

            for key, provider in (
                ("use_cuda", "CUDAExecutionProvider"),
                ("EngineConfig.onnxruntime.use_cuda", "CUDAExecutionProvider"),
                ("use_cann", "CANNExecutionProvider"),
                ("EngineConfig.onnxruntime.use_cann", "CANNExecutionProvider"),
                ("use_dml", "DmlExecutionProvider"),
                ("EngineConfig.onnxruntime.use_dml", "DmlExecutionProvider"),
            ):
                if kwargs.get(key) is True:
                    required_onnx = True
                    needs[provider].append(str(model_name))

        if required_onnx:
            try:
                import onnxruntime

                providers = set(onnxruntime.get_available_providers())
                missing_providers = {
                    provider: model_names
                    for provider, model_names in needs.items()
                    if model_names and provider not in providers
                }
                self.report.add(
                    "runtime.onnxruntime",
                    "error" if missing_providers else "ok",
                    "environment",
                    "ONNX Runtime providers match configuration"
                    if not missing_providers
                    else "ONNX Runtime providers are missing",
                    details={
                        "available_providers": sorted(providers),
                        "missing_providers": missing_providers,
                    },
                )
            except Exception as e:
                self.report.add(
                    "runtime.onnxruntime",
                    "error",
                    "environment",
                    "ONNX Runtime is required but unavailable",
                    details={"error_type": type(e).__name__, "error": str(e)},
                )

        if openvino_models:
            ok = importlib.util.find_spec("openvino") is not None
            self.report.add(
                "runtime.openvino",
                "ok" if ok else "error",
                "environment",
                "OpenVINO is available" if ok else "OpenVINO is required but missing",
                details={"models": openvino_models},
            )

    def _check_llm_configs(self) -> None:
        for name in ("paddle", "glm"):
            model_cfg = self._get(f"model_manager.models.{name}")
            if not isinstance(model_cfg, Mapping):
                continue
            bad_keys = [key for key in ("model", "client", "params") if key in model_cfg]
            if bad_keys:
                patches: list[ConfigPatch] = []
                if "model" in model_cfg:
                    patches.append(
                        ConfigPatch(
                            path=f"model_manager.models.{name}.kwargs.model",
                            value=model_cfg["model"],
                            reason="LLMModel settings must be nested under kwargs",
                        )
                    )
                if isinstance(model_cfg.get("client"), Mapping):
                    client = model_cfg["client"]
                    for key, value in client.items():
                        patches.append(
                            ConfigPatch(
                                path=f"model_manager.models.{name}.kwargs.client.{key}",
                                value=value,
                                reason="LLMModel client settings must be nested under kwargs",
                            )
                        )
                self.report.add(
                    f"model.llm.{name}.shape",
                    "error",
                    "config",
                    "LLMModel configuration contains ignored top-level keys",
                    details={"bad_keys": bad_keys},
                    suggested_patches=patches,
                )
            else:
                self.report.add(
                    f"model.llm.{name}.shape",
                    "ok",
                    "config",
                    "LLMModel configuration shape is valid",
                )

        formula_model = self._get("model_manager.executors.formula.model")
        if formula_model in ("paddle", "glm"):
            base_url = self._get(
                f"model_manager.models.{formula_model}.kwargs.client.base_url"
            )
            if not isinstance(base_url, str) or not base_url:
                self.report.add(
                    "formula.llm.config",
                    "error",
                    "config",
                    "Formula LLM base_url is missing",
                    details={"model": formula_model},
                )
            elif self.args.check_network:
                self._probe_llm(
                    base_url,
                    expected=str(formula_model),
                    check_id="formula.llm.models",
                )

    def _parse_llm(self, text: str) -> LLMInfo:
        text = text.strip()
        if self._looks_like_url(text):
            info = LLMInfo(base_url=text, raw={"base_url": text})
            if self.args.check_network:
                probe = self._probe_llm(text, check_id="llm.models")
                if probe:
                    info.name = probe.name
                    info.model = probe.model
                    info.raw.update({"name": probe.name, "model": probe.model})
            return info

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            self.report.add(
                "llm.parse",
                "error",
                "config",
                "LLM option is neither a URL nor JSON",
                details={"error": str(e)},
            )
            return LLMInfo()

        if not isinstance(data, dict):
            self.report.add(
                "llm.parse",
                "error",
                "config",
                "LLM JSON must be an object",
            )
            return LLMInfo()

        name = data.get("name")
        if name not in ("deepseek", "paddle", "glm"):
            self.report.add(
                "llm.name",
                "error",
                "config",
                "LLM name is unsupported",
                details={"name": name, "supported": ["deepseek", "paddle", "glm"]},
            )
        base_url = data.get("base_url")
        if isinstance(base_url, str) and self.args.check_network:
            self._probe_llm(base_url, expected=name, check_id="llm.models")
        return LLMInfo(
            name=name if isinstance(name, str) else None,
            base_url=base_url if isinstance(base_url, str) else None,
            model=data.get("model") if isinstance(data.get("model"), str) else None,
            api_key=data.get("api_key") if isinstance(data.get("api_key"), str) else None,
            raw=data,
        )

    def _probe_llm(
        self, base_url: str, expected: str | None = None, check_id: str = "llm.models"
    ) -> LLMInfo | None:
        url = base_url.rstrip("/") + "/models"
        try:
            with httpx.Client(timeout=self.args.llm_timeout) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            self.report.add(
                check_id,
                "error",
                "network",
                "LLM /models endpoint is not reachable",
                details={
                    "url": url,
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )
            return None

        model_ids = self._extract_model_ids(data)
        inferred = self._infer_llm_name(model_ids[0] if model_ids else "")
        status: CheckStatus = "ok"
        message = "LLM /models endpoint is reachable"
        patches: list[ConfigPatch] = []
        if not model_ids:
            status = "error"
            message = "LLM /models response has no models"
        elif expected and expected not in (inferred or ""):
            status = "error"
            message = "LLM model does not match the selected backend"
            if inferred:
                patches.append(
                    ConfigPatch(
                        path="parse.backend",
                        value=inferred,
                        reason="Model id appears to match another backend",
                    )
                )

        self.report.add(
            check_id,
            status,
            "network" if status == "error" else "config",
            message,
            details={
                "url": url,
                "expected": expected,
                "model_ids": model_ids,
                "inferred": inferred,
            },
            suggested_patches=patches,
        )
        return LLMInfo(name=inferred, base_url=base_url, model=model_ids[0] if model_ids else None)

    def _apply_formula_settings(self, custom: dict[str, Any], formula: str) -> None:
        if formula == "no":
            return
        if formula in ("paddle", "glm"):
            custom["model_manager.executors.formula.model"] = formula
            return
        if formula == "mfr":
            custom["model_manager.executors.formula.model"] = "formula-mfr"
            return
        if formula == "pp":
            custom["model_manager.executors.formula.model"] = "formula-pp"
            return

        info = self._parse_llm(formula)
        if info.name not in ("paddle", "glm"):
            self.report.add(
                "formula.llm.name",
                "error",
                "config",
                "Formula LLM must use paddle or glm",
                details={"name": info.name},
            )
            return
        custom["model_manager.executors.formula.model"] = info.name
        if info.model:
            custom[f"model_manager.models.{info.name}.kwargs.model"] = info.model
        if info.base_url:
            custom[
                f"model_manager.models.{info.name}.kwargs.client.base_url"
            ] = info.base_url
        if info.api_key:
            custom[
                f"model_manager.models.{info.name}.kwargs.client.api_key"
            ] = info.api_key

    def _get(self, dotted: str) -> Any:
        obj: Any = self._settings
        for part in dotted.split("."):
            if not isinstance(obj, Mapping):
                return None
            obj = obj.get(part)
        return obj

    def _to_enum(self, enum_cls: type[StrEnum], value: Any) -> Any | None:
        if isinstance(value, enum_cls):
            return value
        try:
            return enum_cls(str(value).lower())
        except ValueError:
            return None

    def _parse_pages(self, pages: str) -> list[int]:
        result: set[int] = set()
        for raw in pages.split(","):
            part = raw.strip()
            if not part:
                continue
            if "-" in part:
                left, right = part.split("-", 1)
                start = int(left)
                end = int(right)
                if start <= 0 or end <= 0 or end < start:
                    raise ValueError(f"bad range: {part}")
                result.update(range(start, end + 1))
            else:
                page = int(part)
                if page <= 0:
                    raise ValueError(f"bad page: {part}")
                result.add(page)
        return sorted(result)

    def _extract_model_ids(self, data: Any) -> list[str]:
        if not isinstance(data, Mapping):
            return []
        items = data.get("data")
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            return []
        ids: list[str] = []
        for item in items:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                ids.append(item["id"])
        return ids

    def _infer_llm_name(self, model_id: str) -> str | None:
        model_id = model_id.lower()
        if "deepseek" in model_id:
            return "deepseek"
        if "paddle" in model_id:
            return "paddle"
        if "glm" in model_id:
            return "glm"
        return None

    def _is_path_key(self, key: str) -> bool:
        key = key.lower()
        return (
            key.endswith("_path")
            or key.endswith("_dir")
            or key.endswith("dir_or_path")
            or "model_path" in key
            or "model_dir" in key
            or "root_dir" in key
        )

    def _looks_like_url(self, value: str) -> bool:
        return re.fullmatch(r"https?://.+", value.strip()) is not None

    def _nearest_existing_parent(self, path: Path) -> Path:
        cur = path if path.exists() and path.is_dir() else path.parent
        while not cur.exists():
            parent = cur.parent
            if parent == cur:
                return Path(".")
            cur = parent
        return cur

    def _redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                key_str = str(key)
                if any(word in key_str.lower() for word in ("key", "token", "secret")):
                    result[key_str] = "***"
                else:
                    result[key_str] = self._redact(item)
            return result
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    def _clean_capture(self, text: str, limit: int = 4000) -> str:
        text = _OSC_RE.sub("", text)
        text = _CSI_RE.sub("", text)
        text = _ANSI_RE.sub("", text)
        text = _CONTROL_RE.sub("", text)
        return text[-limit:]
