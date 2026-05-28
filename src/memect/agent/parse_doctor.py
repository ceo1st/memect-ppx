import json
import re
import shlex
import traceback
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from memect.agent.chapter_doctor import (
    ChapterDoctor,
    ChapterDoctorArgs,
    ChapterDoctorReport,
    format_chapter_report_console,
    should_run_chapter_doctor,
)
from memect.agent.doctor import Doctor, DoctorArgs, DoctorReport


DiagnosticStatus = Literal["ok", "info", "warning", "error"]
DiagnosisKind = Literal["ok", "config_error", "code_error", "quality_issue"]
AgentProvider = Literal["auto", "openai-chat", "openai-responses", "anthropic"]


class DiagnosticItem(BaseModel):
    id: str
    status: DiagnosticStatus
    message: str
    details: Mapping[str, Any] = Field(default_factory=dict)


class RelatedConfigItem(BaseModel):
    path: str
    value: Any = None
    source: Literal["config", "parse_param", "cli", "state"] = "config"
    reason: str
    suggestion: str | None = None


class ConfigRelation(BaseModel):
    id: str
    status: DiagnosticStatus
    message: str
    values: Mapping[str, Any] = Field(default_factory=dict)
    suggestion: str | None = None


class TestCommand(BaseModel):
    id: str
    title: str
    command: str
    reason: str


class PageQuality(BaseModel):
    number: int
    object_count: int = 0
    text_object_count: int = 0
    char_count: int = 0
    object_types: dict[str, int] = Field(default_factory=dict)
    empty: bool = False


class DocumentQuality(BaseModel):
    out_dir: Path
    exists: bool = False
    files: dict[str, bool] = Field(default_factory=dict)
    page_count: int = 0
    analyzed_page_count: int = 0
    empty_page_count: int = 0
    total_objects: int = 0
    text_object_count: int = 0
    json_char_count: int = 0
    markdown_char_count: int = 0
    total_char_count: int = 0
    avg_chars_per_page: float = 0
    object_types: dict[str, int] = Field(default_factory=dict)
    pages: list[PageQuality] = Field(default_factory=list)
    state: Mapping[str, Any] = Field(default_factory=dict)


class ParseQualitySummary(BaseModel):
    documents: list[DocumentQuality] = Field(default_factory=list)
    document_count: int = 0
    page_count: int = 0
    analyzed_page_count: int = 0
    empty_page_count: int = 0
    total_objects: int = 0
    text_object_count: int = 0
    total_char_count: int = 0
    avg_chars_per_page: float = 0
    object_types: dict[str, int] = Field(default_factory=dict)
    symptoms: list[DiagnosticItem] = Field(default_factory=list)


class AgentResult(BaseModel):
    ok: bool = False
    model: str | None = None
    provider: str | None = None
    detected_provider: str | None = None
    detection_errors: dict[str, str] = Field(default_factory=dict)
    content: str | None = None
    parsed_json: Any | None = None
    error: str | None = None


class AgentConfig(BaseModel):
    provider: AgentProvider = "auto"
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout: float = 60
    temperature: float | None = 0
    max_completion_tokens: int | None = None
    max_output_tokens: int | None = None
    model_reasoning_effort: str | None = None
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    anthropic_version: str = "2023-06-01"
    thinking: Mapping[str, Any] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    client: Mapping[str, Any] | None = None
    params: Mapping[str, Any] | None = None

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: Any) -> Any:
        aliases = {
            "": "auto",
            "openai": "openai-chat",
            "openai-compatible": "openai-chat",
            "chat": "openai-chat",
            "responses": "openai-responses",
            "openai-response": "openai-responses",
            "response": "openai-responses",
        }
        if isinstance(value, str):
            return aliases.get(value.strip(), value)
        if value is None:
            return "auto"
        return value


class ParseDoctorReport(BaseModel):
    problem: str
    classification: DiagnosisKind = "ok"
    confidence: float = 0
    parse_failed: bool = False
    failure: Mapping[str, Any] | None = None
    doctor: DoctorReport | None = None
    quality: ParseQualitySummary = Field(default_factory=ParseQualitySummary)
    related: list[RelatedConfigItem] = Field(default_factory=list)
    relations: list[ConfigRelation] = Field(default_factory=list)
    test_commands: list[TestCommand] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    agent: AgentResult | None = None
    chapter: ChapterDoctorReport | None = None
    report_files: dict[str, Path] = Field(default_factory=dict)


class ParseDoctorArgs(BaseModel):
    problem: str
    file: Path
    out_dirs: list[Path] = Field(default_factory=list)
    conf_dir: Path = Path("./conf")
    out_dir: Path | None = None
    as_doc: bool = False
    pages: str | None = None
    backend: str | None = None
    llm: str | None = None
    mode: str | None = None
    ocr: str | None = None
    table: str | None = None
    formula: str | None = None
    tree: str | None = None
    cpu: str | None = None
    cuda: str | None = None
    params_text: str | None = None
    params_file: Path | None = None
    custom_settings: dict[str, Any] = Field(default_factory=dict)
    params_snapshot: Mapping[str, Any] = Field(default_factory=dict)
    parse_failed: bool = False
    error: BaseException | None = Field(default=None, exclude=True)
    command: list[str] = Field(default_factory=list)
    agent_config: Path = Path("./agent.json")
    check_network: bool = False

    model_config = {"arbitrary_types_allowed": True}


class ParseDoctor:
    def __init__(self, args: ParseDoctorArgs | Mapping[str, Any]):
        self.args = ParseDoctorArgs.model_validate(args)
        self._settings: Mapping[str, Any] = {}

    def run(self) -> ParseDoctorReport:
        doctor = self._run_config_doctor()
        self._settings = self._settings_from_doctor()

        report = ParseDoctorReport(
            problem=self.args.problem,
            parse_failed=self.args.parse_failed,
            failure=self._failure_info(),
            doctor=doctor,
        )
        report.quality = self._analyze_outputs()
        report.related = self._related_config()
        report.relations = self._config_relations()
        report.test_commands = self._plan_test_commands()
        report.classification, report.confidence = self._classify(report)
        report.notes = self._notes(report)

        if should_run_chapter_doctor(self.args.problem, self.args.mode):
            report.chapter = self._run_chapter_doctor()
        else:
            agent_config = self._agent_config()
            if isinstance(agent_config, AgentResult):
                report.agent = agent_config
            elif agent_config and agent_config.base_url:
                report.agent = AgentClient(agent_config).diagnose(report)

        self._write_report(report)
        return report

    def _run_config_doctor(self) -> DoctorReport:
        return Doctor(
            DoctorArgs(
                file=self.args.file,
                out_dir=self.args.out_dir,
                as_doc=self.args.as_doc,
                pages=self.args.pages,
                backend=self.args.backend,
                llm=self.args.llm,
                mode=self.args.mode,
                ocr=self.args.ocr,
                table=self.args.table,
                formula=self.args.formula,
                tree=self.args.tree,
                params_text=self.args.params_text,
                params_file=self.args.params_file,
                conf_dir=self.args.conf_dir,
                custom_settings=self.args.custom_settings,
                cpu=self.args.cpu,
                cuda=self.args.cuda,
                check_network=self.args.check_network,
            )
        ).run()

    def _run_chapter_doctor(self) -> ChapterDoctorReport:
        return ChapterDoctor(
            ChapterDoctorArgs(
                problem=self.args.problem,
                file=self.args.file,
                out_dirs=self.args.out_dirs,
                out_dir=self.args.out_dir,
                pages=self.args.pages,
                tree=self.args.tree,
                params_snapshot=self.args.params_snapshot,
                command=self.args.command,
                agent_config=self.args.agent_config,
            )
        ).run()

    def _settings_from_doctor(self) -> Mapping[str, Any]:
        # Doctor keeps the effective settings private because its public contract is
        # the report. Loading through it still gives us enough signal for diagnosis:
        # read values back from check details when validation succeeded is brittle,
        # so create a tiny Doctor instance and use its guarded loader path.
        probe = Doctor(
            DoctorArgs(
                file=self.args.file,
                conf_dir=self.args.conf_dir,
                custom_settings=self.args.custom_settings,
                check_network=False,
            )
        )
        probe._apply_device()
        probe._prepare_parse_options()
        probe._load_config()
        settings = getattr(probe, "_settings", None)
        return settings if isinstance(settings, Mapping) else {}

    def _analyze_outputs(self) -> ParseQualitySummary:
        summary = ParseQualitySummary()
        out_dirs = self.args.out_dirs or [self._default_out_dir()]
        requested_pages = self._parse_pages(self.args.pages)
        object_types: Counter[str] = Counter()

        for out_dir in out_dirs:
            doc = self._analyze_one_output(out_dir, requested_pages)
            summary.documents.append(doc)
            summary.document_count += 1
            summary.page_count += doc.page_count
            summary.analyzed_page_count += doc.analyzed_page_count
            summary.empty_page_count += doc.empty_page_count
            summary.total_objects += doc.total_objects
            summary.text_object_count += doc.text_object_count
            summary.total_char_count += doc.total_char_count
            object_types.update(doc.object_types)

        summary.object_types = dict(object_types)
        if summary.analyzed_page_count:
            summary.avg_chars_per_page = round(
                summary.total_char_count / summary.analyzed_page_count, 2
            )
        summary.symptoms = self._detect_quality_symptoms(summary, requested_pages)
        return summary

    def _analyze_one_output(
        self, out_dir: Path, requested_pages: Sequence[int]
    ) -> DocumentQuality:
        doc = DocumentQuality(out_dir=out_dir, exists=out_dir.exists())
        files = {
            "doc.md": out_dir / "doc.md",
            "doc.json": out_dir / "doc.json",
            "state.json": out_dir / "state.json",
        }
        doc.files = {name: path.is_file() for name, path in files.items()}

        markdown = files["doc.md"].read_text("utf-8") if files["doc.md"].is_file() else ""
        doc.markdown_char_count = _content_len(markdown)

        state = _read_json(files["state.json"])
        if isinstance(state, Mapping):
            doc.state = state

        data = _read_json(files["doc.json"])
        pages = data.get("pages") if isinstance(data, Mapping) else None
        if isinstance(pages, Sequence) and not isinstance(pages, (str, bytes)):
            doc.page_count = len(pages)
            selected = set(requested_pages)
            for page in pages:
                if not isinstance(page, Mapping):
                    continue
                number = _as_int(page.get("number"), default=0)
                if selected and number not in selected:
                    continue
                page_quality = self._analyze_page(page)
                doc.pages.append(page_quality)
                doc.analyzed_page_count += 1
                doc.empty_page_count += 1 if page_quality.empty else 0
                doc.total_objects += page_quality.object_count
                doc.text_object_count += page_quality.text_object_count
                doc.json_char_count += page_quality.char_count
                doc.object_types = _counter_add_dict(
                    doc.object_types, page_quality.object_types
                )

        doc.total_char_count = max(doc.markdown_char_count, doc.json_char_count)
        if doc.analyzed_page_count:
            doc.avg_chars_per_page = round(
                doc.total_char_count / doc.analyzed_page_count, 2
            )
        return doc

    def _analyze_page(self, page: Mapping[str, Any]) -> PageQuality:
        objects = page.get("objects")
        counter: Counter[str] = Counter()
        chars = 0
        object_count = 0
        text_object_count = 0

        if isinstance(objects, Sequence) and not isinstance(objects, (str, bytes)):
            for obj in objects:
                stat = _object_stat(obj)
                chars += stat["chars"]
                object_count += stat["objects"]
                text_object_count += stat["text_objects"]
                counter.update(stat["types"])

        return PageQuality(
            number=_as_int(page.get("number"), default=0),
            object_count=object_count,
            text_object_count=text_object_count,
            char_count=chars,
            object_types=dict(counter),
            empty=object_count == 0 and chars == 0,
        )

    def _detect_quality_symptoms(
        self, summary: ParseQualitySummary, requested_pages: Sequence[int]
    ) -> list[DiagnosticItem]:
        symptoms: list[DiagnosticItem] = []
        if not summary.documents:
            symptoms.append(
                DiagnosticItem(
                    id="output.none",
                    status="error",
                    message="没有找到可分析的解析输出目录",
                )
            )
            return symptoms

        missing_doc_json = [
            str(doc.out_dir)
            for doc in summary.documents
            if doc.exists and not doc.files.get("doc.json")
        ]
        missing_doc_md = [
            str(doc.out_dir)
            for doc in summary.documents
            if doc.exists and not doc.files.get("doc.md")
        ]
        missing_dirs = [str(doc.out_dir) for doc in summary.documents if not doc.exists]
        if missing_dirs:
            symptoms.append(
                DiagnosticItem(
                    id="output.dir_missing",
                    status="error",
                    message="解析输出目录不存在，解析可能在写出结果前失败",
                    details={"out_dirs": missing_dirs},
                )
            )
        if missing_doc_json:
            symptoms.append(
                DiagnosticItem(
                    id="output.doc_json_missing",
                    status="warning",
                    message="缺少 doc.json，无法精确统计页面对象",
                    details={"out_dirs": missing_doc_json},
                )
            )
        if missing_doc_md:
            symptoms.append(
                DiagnosticItem(
                    id="output.doc_md_missing",
                    status="warning",
                    message="缺少 doc.md，Markdown 内容可能没有生成",
                    details={"out_dirs": missing_doc_md},
                )
            )

        if summary.analyzed_page_count == 0:
            symptoms.append(
                DiagnosticItem(
                    id="quality.no_pages",
                    status="warning",
                    message="没有可分析的页面，检查 --pages 是否超出文档页数",
                    details={"requested_pages": list(requested_pages)},
                )
            )
        elif summary.empty_page_count == summary.analyzed_page_count:
            symptoms.append(
                DiagnosticItem(
                    id="quality.all_pages_empty",
                    status="warning",
                    message="被分析的页面没有解析对象和文本",
                    details={
                        "analyzed_page_count": summary.analyzed_page_count,
                        "empty_page_count": summary.empty_page_count,
                    },
                )
            )
        elif summary.empty_page_count / summary.analyzed_page_count >= 0.3:
            symptoms.append(
                DiagnosticItem(
                    id="quality.many_empty_pages",
                    status="warning",
                    message="空页面比例偏高",
                    details={
                        "analyzed_page_count": summary.analyzed_page_count,
                        "empty_page_count": summary.empty_page_count,
                    },
                )
            )

        if summary.analyzed_page_count and summary.avg_chars_per_page < 80:
            symptoms.append(
                DiagnosticItem(
                    id="quality.low_chars_per_page",
                    status="warning",
                    message="平均每页文本字符数偏低",
                    details={
                        "avg_chars_per_page": summary.avg_chars_per_page,
                        "total_char_count": summary.total_char_count,
                    },
                )
            )

        if summary.total_objects == 0 and summary.analyzed_page_count:
            symptoms.append(
                DiagnosticItem(
                    id="quality.no_objects",
                    status="warning",
                    message="页面没有解析出任何对象",
                )
            )

        if (
            summary.analyzed_page_count
            and summary.text_object_count == 0
            and summary.total_char_count < 200
        ):
            symptoms.append(
                DiagnosticItem(
                    id="quality.no_text_objects",
                    status="warning",
                    message="文本对象数量为 0，可能需要强制 OCR 或调整渲染/OCR 参数",
                    details={"object_types": summary.object_types},
                )
            )

        for doc in summary.documents:
            pdf2image = doc.state.get("pdf2image")
            if isinstance(pdf2image, Mapping):
                total = _as_int(pdf2image.get("total"), default=0)
                success = _as_int(pdf2image.get("success"), default=total)
                if total and success < total:
                    symptoms.append(
                        DiagnosticItem(
                            id="state.pdf2image_incomplete",
                            status="warning",
                            message="PDF 转图片没有全部成功",
                            details={
                                "out_dir": str(doc.out_dir),
                                "total": total,
                                "success": success,
                            },
                        )
                    )
        return symptoms

    def _related_config(self) -> list[RelatedConfigItem]:
        items: list[RelatedConfigItem] = []
        params = self.args.params_snapshot

        for path, reason, suggestion in (
            ("backend", "解析后端决定走本地默认模型还是 LLM 后端", "先用 default 和当前后端各跑少量页对比"),
            ("ocr", "扫描件或图片型 PDF 内容少时，OCR 模式通常是首要变量", "用 --ocr yes 跑 1-3 页验证"),
            ("mode", "tree/ppt/page 模式会影响最终组织和输出", "内容抽取问题先用 page 模式隔离"),
            ("table", "表格识别方式可能影响表格内文字是否进入输出", "表格密集文档可对比 --table auto/no"),
            ("formula", "公式识别失败时可能影响公式区域输出，但通常不是全文为空的主因", None),
            ("pagenos", "--pages 只解析指定页，容易造成看起来内容少", "确认测试页包含正文"),
        ):
            value = params.get(path)
            if value is not None:
                items.append(
                    RelatedConfigItem(
                        path=f"parse_params.{path}",
                        value=value,
                        source="parse_param",
                        reason=reason,
                        suggestion=suggestion,
                    )
                )

        config_items = (
            (
                "pdf_parser.pdf2image.max_size",
                "PDF 渲染图片的最大宽高，影响 OCR/版面模型实际看到的像素",
                "小字密集文档可提高长边，但要和 OCR Global.max_side_len 对齐",
            ),
            (
                "pdf_parser.pdf2image.max_scale",
                "PDF 渲染缩放上限，影响小字清晰度和显存/内存占用",
                "先用少量页测试更高 scale，再观察耗时和内存",
            ),
            (
                "pdf_parser.default.pdf.provider",
                "默认 PDF 文本/图像抽取 provider 会影响原生 PDF 字符获取",
                "原生 PDF 字符缺失时对比 provider",
            ),
            (
                'model_manager.models.ocr.kwargs."Global.max_side_len"',
                "OCR 全局允许的最大边长，应覆盖 pdf2image 渲染出的长边",
                "保持 max(pdf2image.max_size) <= Global.max_side_len",
            ),
            (
                'model_manager.models.ocr.kwargs."Global.text_score"',
                "OCR 文本置信度阈值，过高会过滤低置信文字",
                "内容偏少时可小幅降低后对比误识别率",
            ),
            (
                'model_manager.models.ocr.kwargs."Det.limit_side_len"',
                "OCR 检测模型输入尺寸，影响小字和密集文本检测",
                "密集小字可尝试提高并只跑少量页验证",
            ),
            (
                'model_manager.models.ocr.kwargs."Det.limit_type"',
                "OCR 检测 resize 策略，和 Det.limit_side_len 配合生效",
                None,
            ),
            (
                'model_manager.models.ocr.kwargs."Det.box_thresh"',
                "文本框检测阈值，过高可能漏检低置信文本框",
                "漏字明显时可小幅降低并对比误检",
            ),
            (
                "model_manager.executors.ocr.max_workers",
                "OCR 并发影响资源压力，资源不足时可能导致失败或结果不稳定",
                "诊断时先降低并发以排除资源问题",
            ),
            (
                "model_manager.executors.layout.max_workers",
                "版面模型并发影响资源压力",
                "诊断时先降低并发以排除资源问题",
            ),
        )
        for path, reason, suggestion in config_items:
            items.append(
                RelatedConfigItem(
                    path=path,
                    value=self._get_config(path),
                    reason=reason,
                    suggestion=suggestion,
                )
            )
        return items

    def _config_relations(self) -> list[ConfigRelation]:
        relations: list[ConfigRelation] = []
        max_size = self._get_config("pdf_parser.pdf2image.max_size")
        ocr_max_side = self._get_config(
            'model_manager.models.ocr.kwargs."Global.max_side_len"'
        )
        render_long_side = _max_number(max_size)
        ocr_max_side_num = _as_int(ocr_max_side)
        values = {
            "pdf_parser.pdf2image.max_size": max_size,
            'model_manager.models.ocr.kwargs."Global.max_side_len"': ocr_max_side,
            "render_long_side": render_long_side,
        }
        if render_long_side is None or ocr_max_side_num is None:
            relations.append(
                ConfigRelation(
                    id="render_size_vs_ocr_max_side",
                    status="warning",
                    message="无法确认 PDF 渲染长边和 OCR 最大边长是否对齐",
                    values=values,
                    suggestion="确认 pdf_parser.pdf2image.max_size 和 OCR Global.max_side_len 都是数字配置",
                )
            )
        elif render_long_side <= ocr_max_side_num:
            relations.append(
                ConfigRelation(
                    id="render_size_vs_ocr_max_side",
                    status="ok",
                    message="PDF 渲染长边没有超过 OCR Global.max_side_len",
                    values=values,
                    suggestion="这两个配置有关联：渲染出的图片长边应小于等于 OCR 可处理长边",
                )
            )
        else:
            relations.append(
                ConfigRelation(
                    id="render_size_vs_ocr_max_side",
                    status="warning",
                    message="PDF 渲染长边超过 OCR Global.max_side_len，OCR 可能会二次缩放导致小字变差",
                    values=values,
                    suggestion=(
                        "提高 model_manager.models.ocr.kwargs.\"Global.max_side_len\" "
                        "或降低 pdf_parser.pdf2image.max_size 的长边"
                    ),
                )
            )
        return relations

    def _plan_test_commands(self) -> list[TestCommand]:
        pages = self.args.pages or "1-3"
        base_out = self._default_out_dir() / "agent" / "doctor-tests"
        commands = [
            TestCommand(
                id="ocr_yes",
                title="强制 OCR 少量页",
                command=self._parse_command(
                    pages=pages,
                    out_dir=base_out / "ocr_yes",
                    extra_flags={"ocr": "yes"},
                ),
                reason="判断内容少是否由 OCR 自动判断没有触发或原生 PDF 文本抽取不足导致",
            ),
            TestCommand(
                id="page_mode",
                title="使用 page 模式隔离结构化影响",
                command=self._parse_command(
                    pages=pages,
                    out_dir=base_out / "page_mode",
                    extra_flags={"mode": "page"},
                ),
                reason="先排除 tree/ppt 后处理对最终输出的影响",
            ),
            TestCommand(
                id="render_ocr_size",
                title="对齐渲染长边和 OCR 最大边长",
                command=self._parse_command(
                    pages=pages,
                    out_dir=base_out / "render_ocr_size",
                    extra_sets={
                        "pdf_parser.pdf2image.max_size": [2500, 7000],
                        'model_manager.models.ocr.kwargs."Global.max_side_len"': 7000,
                    },
                ),
                reason="验证小字密集页面是否因为渲染尺寸/OCR尺寸联动导致漏识别",
            ),
            TestCommand(
                id="ocr_threshold",
                title="降低 OCR 文本过滤阈值",
                command=self._parse_command(
                    pages=pages,
                    out_dir=base_out / "ocr_threshold",
                    extra_sets={
                        'model_manager.models.ocr.kwargs."Global.text_score"': 0.3,
                        'model_manager.models.ocr.kwargs."Det.box_thresh"': 0.4,
                    },
                ),
                reason="验证内容少是否由检测/识别置信度阈值过滤造成",
            ),
        ]
        backend = self.args.backend or self.args.params_snapshot.get("backend")
        if backend and str(backend) != "default":
            commands.append(
                TestCommand(
                    id="backend_default",
                    title="对比 default 后端",
                    command=self._parse_command(
                        pages=pages,
                        out_dir=base_out / "backend_default",
                        extra_flags={"backend": "default"},
                    ),
                    reason="判断内容少是否只发生在当前 LLM 后端",
                )
            )
        return commands

    def _parse_command(
        self,
        *,
        pages: str,
        out_dir: Path,
        extra_flags: Mapping[str, Any] | None = None,
        extra_sets: Mapping[str, Any] | None = None,
    ) -> str:
        parts: list[str] = ["ppx", "parse", str(self.args.file), "--pages", pages]
        parts.extend(["-o", str(out_dir)])
        if self.args.conf_dir != Path("./conf"):
            parts.extend(["--conf", str(self.args.conf_dir)])

        flags = {
            "backend": self.args.backend,
            "mode": self.args.mode,
            "ocr": self.args.ocr,
            "table": self.args.table,
            "formula": self.args.formula,
            "tree": self.args.tree,
        }
        flags.update(extra_flags or {})
        for name, value in flags.items():
            if value is not None:
                parts.extend([f"--{name.replace('_', '-')}", str(value)])

        settings = dict(self.args.custom_settings)
        settings.update(extra_sets or {})
        for key, value in settings.items():
            if _is_secret_path(key):
                value = "<redacted>"
            parts.extend(["--set", _kv_text(key, value)])

        return " ".join(shlex.quote(part) for part in parts)

    def _classify(self, report: ParseDoctorReport) -> tuple[DiagnosisKind, float]:
        doctor_errors = []
        if report.doctor is not None:
            doctor_errors = [
                check for check in report.doctor.checks if check.status == "error"
            ]

        if self.args.parse_failed:
            if doctor_errors:
                return "config_error", 0.85
            return "code_error", 0.65

        if report.quality.symptoms or _looks_like_quality_problem(self.args.problem):
            return "quality_issue", 0.75 if report.quality.symptoms else 0.45

        return "ok", 0.6

    def _notes(self, report: ParseDoctorReport) -> list[str]:
        notes: list[str] = []
        if report.classification == "config_error":
            notes.append("基础 doctor 已发现配置/环境/输入错误，优先按 suggested_patches 修正后重跑。")
        elif report.classification == "code_error":
            notes.append("基础 doctor 未发现明确配置错误，当前更像解析流程代码异常；报告中保留了 traceback。")
        elif report.classification == "quality_issue":
            notes.append("解析流程完成但质量指标偏低，优先用报告中的少量页命令做 A/B 测试。")

        notes.append(
            'pdf_parser.pdf2image.max_size 和 model_manager.models.ocr.kwargs."Global.max_side_len" '
            "有关联：渲染图片长边应不超过 OCR 可处理长边。"
        )
        return notes

    def _failure_info(self) -> Mapping[str, Any] | None:
        if self.args.error is None:
            return None
        return {
            "error_type": type(self.args.error).__name__,
            "error": str(self.args.error),
            "traceback": "".join(
                traceback.format_exception(self.args.error)
            )[-12000:],
        }

    def _write_report(self, report: ParseDoctorReport) -> None:
        out_dir = self._report_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        json_file = out_dir / "doctor-report.json"
        md_file = out_dir / "doctor-report.md"
        report.report_files = {"json": json_file, "markdown": md_file}
        json_file.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_file.write_text(format_report_markdown(report), encoding="utf-8")

    def _report_dir(self) -> Path:
        if self.args.out_dirs:
            return self.args.out_dirs[0] / "agent"
        return self._default_out_dir() / "agent"

    def _default_out_dir(self) -> Path:
        if self.args.out_dir is not None:
            return self.args.out_dir
        return Path(f"{self.args.file}.out")

    def _get_config(self, dotted: str) -> Any:
        obj: Any = self._settings
        for part in _split_dotted(dotted):
            if not isinstance(obj, Mapping):
                return None
            obj = obj.get(part)
        return obj

    def _parse_pages(self, pages: str | None) -> list[int]:
        if not pages:
            return []
        result: set[int] = set()
        for raw in pages.split(","):
            part = raw.strip()
            if not part:
                continue
            if "-" in part:
                left, right = part.split("-", 1)
                start = int(left)
                end = int(right)
                result.update(range(start, end + 1))
            else:
                result.add(int(part))
        return sorted(result)

    def _agent_config(self) -> AgentConfig | AgentResult | None:
        data: dict[str, Any] = {}
        if self.args.agent_config.is_file():
            try:
                raw = json.loads(self.args.agent_config.read_text("utf-8"))
            except Exception as e:
                return AgentResult(
                    ok=False,
                    error=(
                        f"agent config load failed: {self.args.agent_config}: "
                        f"{type(e).__name__}: {e}"
                    ),
                )
            if not isinstance(raw, Mapping):
                return AgentResult(
                    ok=False,
                    error=f"agent config must be a JSON object: {self.args.agent_config}",
                )
            data.update(raw)

        if not data:
            return None
        if "url" in data and "base_url" not in data:
            data["base_url"] = data["url"]
        if "key" in data and "api_key" not in data:
            data["api_key"] = data["key"]

        try:
            return AgentConfig.model_validate(data)
        except Exception as e:
            return AgentResult(
                ok=False,
                error=(
                    f"agent config is invalid: {self.args.agent_config}: "
                    f"{type(e).__name__}: {e}"
                ),
            )


class AgentClient:
    def __init__(self, config: AgentConfig):
        assert config.base_url is not None
        self.config = config
        self._detected_provider: str | None = None
        self._detection_errors: dict[str, str] = {}

    def diagnose(self, report: ParseDoctorReport) -> AgentResult:
        try:
            provider = self._resolve_provider()
            if provider == "anthropic":
                return self._diagnose_anthropic(report)
            if provider == "openai-responses":
                return self._diagnose_openai_responses(report)
            return self._diagnose_openai_chat(report)
        except Exception as e:
            return AgentResult(
                ok=False,
                provider=self.config.provider,
                detected_provider=self._detected_provider,
                detection_errors=self._detection_errors,
                model=self.config.model,
                error=f"{type(e).__name__}: {e}",
            )

    def _diagnose_openai_chat(self, report: ParseDoctorReport) -> AgentResult:
        client = self._openai_client()
        model = self._model()
        params: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._prompt(report)},
            ],
        }
        if self.config.temperature is not None:
            params["temperature"] = self.config.temperature
        if self.config.max_completion_tokens is not None:
            params["max_completion_tokens"] = self.config.max_completion_tokens
        elif self.config.max_tokens is not None:
            params["max_tokens"] = self.config.max_tokens
        effort = self._reasoning_effort()
        if effort:
            params["reasoning_effort"] = effort
        params.update(self.config.params or {})

        response = client.chat.completions.create(**params)
        content = response.choices[0].message.content or ""
        return AgentResult(
            ok=True,
            provider=self.config.provider,
            detected_provider=self._detected_provider,
            detection_errors=self._detection_errors,
            model=model,
            content=content,
            parsed_json=_maybe_json(content),
        )

    def _diagnose_openai_responses(self, report: ParseDoctorReport) -> AgentResult:
        client = self._openai_client()
        model = self._model()
        params: dict[str, Any] = {
            "model": model,
            "instructions": self._system_prompt(),
            "input": self._prompt(report),
        }
        if self.config.temperature is not None:
            params["temperature"] = self.config.temperature
        if self.config.max_output_tokens is not None:
            params["max_output_tokens"] = self.config.max_output_tokens
        elif self.config.max_tokens is not None:
            params["max_output_tokens"] = self.config.max_tokens
        effort = self._reasoning_effort()
        if effort:
            params["reasoning"] = {"effort": effort}
        params.update(self.config.params or {})

        response = client.responses.create(**params)
        content = self._extract_openai_response_text(response)
        return AgentResult(
            ok=True,
            provider=self.config.provider,
            detected_provider=self._detected_provider,
            detection_errors=self._detection_errors,
            model=model,
            content=content,
            parsed_json=_maybe_json(content),
        )

    def _diagnose_anthropic(self, report: ParseDoctorReport) -> AgentResult:
        client = self._anthropic_client()
        model = self._model()
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": self.config.max_tokens or 4096,
            "system": self._system_prompt(),
            "messages": [{"role": "user", "content": self._prompt(report)}],
        }
        if self.config.temperature is not None:
            params["temperature"] = self.config.temperature
        if self.config.thinking:
            params["thinking"] = dict(self.config.thinking)
        params.update(self.config.params or {})

        response = client.messages.create(**params)
        content = self._extract_anthropic_content(response)
        return AgentResult(
            ok=True,
            provider=self.config.provider,
            detected_provider=self._detected_provider,
            detection_errors=self._detection_errors,
            model=model,
            content=content,
            parsed_json=_maybe_json(content),
        )

    def _resolve_provider(self) -> str:
        if self.config.provider != "auto":
            self._detected_provider = self.config.provider
            return self.config.provider

        base_url = (self.config.base_url or "").lower()
        if "anthropic.com" in base_url:
            self._probe_anthropic()
            self._detected_provider = "anthropic"
            return "anthropic"

        probes = (
            ("openai-responses", self._probe_openai_responses),
            ("openai-chat", self._probe_openai_chat),
            ("anthropic", self._probe_anthropic),
        )
        for provider, probe in probes:
            try:
                probe()
            except Exception as e:
                self._detection_errors[provider] = f"{type(e).__name__}: {e}"
                continue
            self._detected_provider = provider
            return provider
        raise RuntimeError(
            "cannot detect agent provider; set provider to openai-chat, "
            "openai-responses, or anthropic"
        )

    def _probe_openai_responses(self) -> None:
        client = self._openai_client()
        client.responses.create(
            model=self._model(),
            input="ping",
            max_output_tokens=1,
            timeout=self._probe_timeout(),
        )

    def _probe_openai_chat(self) -> None:
        client = self._openai_client()
        client.chat.completions.create(
            model=self._model(),
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            timeout=self._probe_timeout(),
        )

    def _probe_anthropic(self) -> None:
        client = self._anthropic_client()
        client.messages.create(
            model=self._model(),
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
            timeout=self._probe_timeout(),
        )

    def _probe_timeout(self) -> float:
        return min(self.config.timeout, 10)

    def _openai_client(self) -> Any:
        try:
            from openai import OpenAI
        except ModuleNotFoundError as e:
            raise RuntimeError("openai SDK is required for openai agent providers") from e

        kwargs: dict[str, Any] = dict(self.config.client or {})
        kwargs["base_url"] = self.config.base_url
        if self.config.api_key is not None:
            kwargs["api_key"] = self.config.api_key
        if self.config.timeout is not None:
            kwargs.setdefault("timeout", self.config.timeout)
        if self.config.headers:
            kwargs.setdefault("default_headers", self.config.headers)
        return OpenAI(**kwargs)

    def _anthropic_client(self) -> Any:
        try:
            from anthropic import Anthropic
        except ModuleNotFoundError as e:
            raise RuntimeError("anthropic SDK is required for anthropic agent provider") from e

        kwargs: dict[str, Any] = dict(self.config.client or {})
        kwargs["base_url"] = self.config.base_url
        if self.config.api_key is not None:
            kwargs["api_key"] = self.config.api_key
        if self.config.timeout is not None:
            kwargs.setdefault("timeout", self.config.timeout)
        headers = dict(self.config.headers)
        if self.config.anthropic_version:
            headers.setdefault("anthropic-version", self.config.anthropic_version)
        if headers:
            kwargs.setdefault("default_headers", headers)
        return Anthropic(**kwargs)

    def _model(self) -> str:
        if not self.config.model:
            raise ValueError(f"agent model is required for {self.config.provider}")
        return self.config.model

    def _reasoning_effort(self) -> str | None:
        return self.config.model_reasoning_effort or self.config.reasoning_effort

    def _extract_openai_response_text(self, response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str):
            return output_text

        data = _model_dump(response)
        output = data.get("output") if isinstance(data, Mapping) else None
        if isinstance(output, Sequence) and not isinstance(output, (str, bytes)):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, Mapping):
                    continue
                content = item.get("content")
                if not isinstance(content, Sequence) or isinstance(
                    content, (str, bytes)
                ):
                    continue
                for block in content:
                    if not isinstance(block, Mapping):
                        continue
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "\n".join(parts)
        return json.dumps(data, ensure_ascii=False)

    def _extract_anthropic_content(self, response: Any) -> str:
        content = getattr(response, "content", None)
        if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            parts: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
                    continue
                data = _model_dump(item)
                text = data.get("text") if isinstance(data, Mapping) else None
                if isinstance(text, str):
                    parts.append(text)
            if parts:
                return "\n".join(parts)
        data = _model_dump(response)
        return json.dumps(data, ensure_ascii=False)

    def _system_prompt(self) -> str:
        return (
            "你是 ppx PDF 解析诊断 agent。只根据用户给出的报告诊断，"
            "不要执行命令。输出 JSON，包含 diagnosis、root_causes、"
            "config_patches、test_plan、explanation。"
        )

    def _prompt(self, report: ParseDoctorReport) -> str:
        data = report.model_dump(mode="json", exclude={"agent", "report_files"})
        text = json.dumps(data, ensure_ascii=False)
        if len(text) > 30000:
            text = text[:30000] + "...<truncated>"
        return text


def format_report_markdown(report: ParseDoctorReport) -> str:
    lines = [
        "# ppx parse doctor",
        "",
        f"- problem: {report.problem}",
        f"- classification: {report.classification}",
        f"- confidence: {report.confidence}",
        f"- parse_failed: {report.parse_failed}",
        "",
        "## quality",
        "",
        f"- documents: {report.quality.document_count}",
        f"- pages: {report.quality.analyzed_page_count}/{report.quality.page_count}",
        f"- chars: {report.quality.total_char_count}",
        f"- avg_chars_per_page: {report.quality.avg_chars_per_page}",
        f"- object_types: {json.dumps(report.quality.object_types, ensure_ascii=False)}",
        "",
    ]
    if report.quality.symptoms:
        lines.extend(["## symptoms", ""])
        for symptom in report.quality.symptoms:
            lines.append(f"- [{symptom.status}] {symptom.id}: {symptom.message}")
        lines.append("")

    if report.relations:
        lines.extend(["## config relations", ""])
        for relation in report.relations:
            lines.append(f"- [{relation.status}] {relation.id}: {relation.message}")
            if relation.suggestion:
                lines.append(f"  suggestion: {relation.suggestion}")
        lines.append("")

    if report.test_commands:
        lines.extend(["## test commands", ""])
        for command in report.test_commands:
            lines.append(f"### {command.id}")
            lines.append("")
            lines.append(command.reason)
            lines.append("")
            lines.append("```bash")
            lines.append(command.command)
            lines.append("```")
            lines.append("")

    if report.agent:
        lines.extend(["## agent", ""])
        lines.append(f"- ok: {report.agent.ok}")
        if report.agent.error:
            lines.append(f"- error: {report.agent.error}")
        if report.agent.content:
            lines.append("")
            lines.append(report.agent.content)
            lines.append("")

    if report.chapter:
        lines.extend(["## chapter doctor", ""])
        lines.append(f"- candidates: {len(report.chapter.candidates)}")
        lines.append(
            "- files: "
            + ", ".join(
                f"{name}={path}" for name, path in report.chapter.report_files.items()
            )
        )
        lines.append("")
        for index, candidate in enumerate(report.chapter.candidates[:20], start=1):
            pages = ""
            if candidate.page_start is not None and candidate.page_end is not None:
                pages = f"{candidate.page_start}-{candidate.page_end}"
            elif candidate.page_start is not None:
                pages = str(candidate.page_start)
            lines.append(
                f"- {index}. {candidate.title} pages={pages} "
                f"source={candidate.source} confidence={candidate.confidence}"
            )
        lines.append("")

    if report.failure:
        lines.extend(["## failure", ""])
        lines.append(f"- type: {report.failure.get('error_type')}")
        lines.append(f"- error: {report.failure.get('error')}")
        lines.append("")
    return "\n".join(lines)


def format_report_console(report: ParseDoctorReport) -> str:
    lines = [
        f"doctor classification={report.classification}, confidence={report.confidence}",
        (
            "quality: "
            f"pages={report.quality.analyzed_page_count}, "
            f"chars={report.quality.total_char_count}, "
            f"avg_chars/page={report.quality.avg_chars_per_page}, "
            f"objects={report.quality.total_objects}"
        ),
    ]
    for symptom in report.quality.symptoms[:5]:
        lines.append(f"[{symptom.status}] {symptom.id}: {symptom.message}")
    for relation in report.relations:
        lines.append(f"[{relation.status}] {relation.id}: {relation.message}")
    if report.test_commands:
        lines.append("test commands:")
        for command in report.test_commands[:4]:
            lines.append(f"- {command.id}: {command.command}")
    if report.agent:
        if report.agent.ok:
            lines.append("agent: ok")
            if report.agent.content:
                lines.append(report.agent.content[:1200])
        else:
            lines.append(f"agent: {report.agent.error}")
    if report.chapter:
        lines.append(format_chapter_report_console(report.chapter))
    if report.report_files:
        lines.append(
            "report: "
            + ", ".join(f"{name}={path}" for name, path in report.report_files.items())
        )
    return "\n".join(lines)


def _read_json(file: Path) -> Any | None:
    if not file.is_file():
        return None
    try:
        import orjson

        return orjson.loads(file.read_bytes())
    except ModuleNotFoundError:
        return json.loads(file.read_text("utf-8"))
    except Exception:
        return None


def _content_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _object_stat(obj: Any) -> dict[str, Any]:
    types: Counter[str] = Counter()
    chars = 0
    objects = 0
    text_objects = 0

    if isinstance(obj, Mapping):
        typ = obj.get("type")
        if isinstance(typ, str):
            types[typ] += 1
            objects += 1
            if typ in {"text", "title", "toc", "other_text", "header", "footer", "footnote"}:
                text_objects += 1
        text = obj.get("text")
        if isinstance(text, str):
            chars += _content_len(text)
        latex = obj.get("latex")
        if isinstance(latex, str):
            chars += _content_len(latex)

        for key in ("objects", "cells", "children"):
            children = obj.get(key)
            if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
                for child in children:
                    child_stat = _object_stat(child)
                    chars += child_stat["chars"]
                    objects += child_stat["objects"]
                    text_objects += child_stat["text_objects"]
                    types.update(child_stat["types"])

    return {
        "chars": chars,
        "objects": objects,
        "text_objects": text_objects,
        "types": types,
    }


def _counter_add_dict(left: Mapping[str, int], right: Mapping[str, int]) -> dict[str, int]:
    counter: Counter[str] = Counter(left)
    counter.update(right)
    return dict(counter)


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _max_number(value: Any) -> int | float | None:
    if isinstance(value, int | float):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        numbers = [v for v in value if isinstance(v, int | float)]
        if numbers:
            return max(numbers)
    return None


def _kv_text(key: str, value: Any) -> str:
    return f"{key}={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"


def _is_secret_path(path: str) -> bool:
    lowered = path.lower()
    return any(word in lowered for word in ("key", "token", "secret", "password"))


def _split_dotted(path: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escape = False
    for ch in path:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
            continue
        if ch in {"'", '"'}:
            quote = ch
            continue
        if ch == ".":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _maybe_json(text: str | None) -> Any | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return value
    return {"value": str(value)}


def _looks_like_quality_problem(problem: str) -> bool:
    problem = problem.lower()
    keywords = ("内容太少", "内容少", "空", "漏", "missing", "too little", "empty")
    return any(keyword in problem for keyword in keywords)
