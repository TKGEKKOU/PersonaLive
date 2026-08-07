"""Voice asset training workflow (dataset prep -> GPT-SoVITS training).

Pipeline:
  1. prepare_dataset: normalize audio to 32 kHz mono WAV + write .list
  2. label_with_asr: transcribe audio with the app's own Qwen3-ASR service
  3. run GPT-SoVITS preprocessing (text/hubert/sv/semantic)
  4. train GPT (s1) + SoVITS (s2) using the installation's runtime python
  5. collect ckpt/pth into the voice asset directory and mark READY

All commands run with the project-bundled GPT-SoVITS runtime; no source code
of the distribution is modified.
"""

import json
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

from voice.gpt_sovits.config import GPTSoVITSConfig


ASSET_STATUS_CREATED = "created"
ASSET_STATUS_PROCESSING = "processing"
ASSET_STATUS_READY = "ready"
ASSET_STATUS_FAILED = "failed"

GPT_SOVITS_SAMPLE_RATE = 32000
ASR_SERVICE_URL = "http://127.0.0.1:17004/transcribe"


class TrainingService:
    """Manages one voice asset training run at a time (single local GPU)."""

    def __init__(self, project_root: Path, config: GPTSoVITSConfig) -> None:
        self.project_root = Path(project_root).resolve()
        self.config = config
        self.assets_root = self.project_root / "data" / "gpt_sovits" / "voices"
        self._lock = threading.Lock()
        self._active_asset_id: str | None = None
        self._dir_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------

    def asset_dir(self, asset_id: str) -> Path:
        return self.assets_root / self._dir_name(asset_id)

    def _dir_name(self, asset_id: str) -> str:
        """Resolve the on-disk directory name for an asset: a memorable
        ``dir_name`` when set (e.g. built-in voices), otherwise the id."""

        cached = self._dir_cache.get(asset_id)
        if cached is not None:
            return cached
        name = asset_id
        try:
            session_factory = self._db_session()
            from app.models import VoiceAsset

            with session_factory() as session:
                asset = session.get(VoiceAsset, asset_id)
                if asset is not None and asset.dir_name:
                    name = asset.dir_name
        except Exception:
            pass
        self._dir_cache[asset_id] = name
        return name

    def dataset_dir(self, asset_id: str) -> Path:
        return self.asset_dir(asset_id) / "dataset"

    def output_dir(self, asset_id: str) -> Path:
        return self.asset_dir(asset_id) / "output"

    def _db_session(self):
        from app.database import build_engine, build_session_factory
        from settings import Settings

        settings = Settings.load()
        engine = build_engine(settings)
        session_factory = build_session_factory(engine)
        return session_factory

    # ------------------------------------------------------------------
    # dataset preparation
    # ------------------------------------------------------------------

    def prepare_dataset(
        self,
        asset_id: str,
        audio_paths: list[str],
        texts: list[str] | None = None,
        speaker: str = "asset",
        language: str = "ZH",
    ) -> dict:
        """Normalize audio to GPT-SoVITS format (32 kHz mono WAV) and write a
        training .list file. Returns dataset stats."""

        dataset = self.dataset_dir(asset_id)
        dataset.mkdir(parents=True, exist_ok=True)
        ffmpeg = self._ffmpeg_path()
        rows = []
        prepared: list[Path] = []
        for index, source in enumerate(audio_paths, start=1):
            source_path = Path(source)
            if not source_path.is_file():
                raise FileNotFoundError(f"参考音频不存在：{source}")
            target = dataset / f"{index:03d}.wav"
            subprocess.run(
                [
                    ffmpeg, "-y", "-i", str(source_path),
                    "-ar", str(GPT_SOVITS_SAMPLE_RATE), "-ac", "1",
                    "-acodec", "pcm_s16le", str(target),
                ],
                check=True,
                capture_output=True,
            )
            prepared.append(target)
            text = (texts[index - 1] if texts and index - 1 < len(texts) else "").strip()
            rows.append(f"{target.as_posix()}|{speaker}|{language}|{text}")
        list_path = dataset / f"{asset_id}.list"
        list_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return {
            "count": len(prepared),
            "dataset_dir": str(dataset),
            "list_file": str(list_path),
        }

    def _ffmpeg_path(self) -> str:
        bundled = self.project_root / "runtime" / "ffmpeg" / "ffmpeg.exe"
        return str(bundled) if bundled.is_file() else "ffmpeg"

    def label_with_asr(self, asset_id: str, language: str = "zh") -> dict:
        """Transcribe each dataset WAV with the app's Qwen3-ASR service and
        rewrite the .list with real text labels."""

        dataset = self.dataset_dir(asset_id)
        list_path = dataset / f"{asset_id}.list"
        if not list_path.is_file():
            raise FileNotFoundError("请先准备数据集")
        lang_map = {"zh": "ZH", "ja": "JA", "en": "EN", "yue": "Cantonese"}
        labeled = []
        for line in list_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            wav_path, speaker, _, _old_text = line.split("|", 3)
            wav_file = Path(wav_path)
            if not wav_file.is_file():
                continue
            payload = wav_file.read_bytes()
            request = Request(
                ASR_SERVICE_URL,
                data=payload,
                headers={"Content-Type": "audio/wav", "x-audio-filename": wav_file.name},
                method="POST",
            )
            try:
                with urlopen(request, timeout=120) as response:
                    result = json.loads(response.read().decode("utf-8"))
                text = str(result.get("text", "")).strip()
            except Exception as exc:
                raise RuntimeError(
                    f"ASR 标注失败（请确认设置页本地语音识别已就绪）：{exc}"
                ) from exc
            lang = lang_map.get(str(result.get("language", "")).lower(), language.upper())
            labeled.append(f"{wav_file.as_posix()}|{speaker}|{lang}|{text}")
        list_path.write_text("\n".join(labeled) + "\n", encoding="utf-8")
        return {"labeled": len(labeled), "list_file": str(list_path)}

    # ------------------------------------------------------------------
    # training orchestration
    # ------------------------------------------------------------------

    def start_training(self, asset_id: str) -> bool:
        with self._lock:
            if self._active_asset_id is not None:
                return False
            self._active_asset_id = asset_id
        threading.Thread(
            target=self._run_training,
            args=(asset_id,),
            name=f"gpt-sovits-train-{asset_id[:8]}",
            daemon=True,
        ).start()
        return True

    def _run_training(self, asset_id: str) -> None:
        try:
            self._update_asset(asset_id, ASSET_STATUS_PROCESSING, "准备数据集")
            commands = self.build_training_commands(asset_id)
            for stage, command, env in commands:
                self._update_asset(asset_id, ASSET_STATUS_PROCESSING, stage)
                self._run_command(asset_id, command, env)
            self._collect_outputs(asset_id)
            self._update_asset(asset_id, ASSET_STATUS_READY, "训练完成")
        except Exception as exc:
            message = str(exc)
            log_path = self.asset_dir(asset_id) / "training.log"
            if log_path.is_file():
                tail = [
                    line
                    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if line.strip()
                ][-15:]
                if tail:
                    message = f"{message}\n--- 日志尾部 ---\n" + "\n".join(tail)
            self._update_asset(asset_id, ASSET_STATUS_FAILED, "训练失败", message)
        finally:
            with self._lock:
                if self._active_asset_id == asset_id:
                    self._active_asset_id = None

    def _training_env(self, asset_id: str) -> dict:
        dataset = self.dataset_dir(asset_id)
        return {
            "inp_text": str(dataset / f"{asset_id}.list"),
            "inp_wav_dir": str(dataset),
            "exp_name": asset_id,
            "opt_dir": str(dataset / "exp"),
            "i_part": "0",
            "all_parts": "1",
            "is_half": "True",
            "_CUDA_VISIBLE_DEVICES": "0",
            "hz": "25hz",
        }

    def build_training_commands(
        self, asset_id: str
    ) -> list[tuple[str, list[str], dict]]:
        """Return [(stage_label, command, env)] for this asset."""

        probe = self.config.probe()
        if not probe.ok:
            raise RuntimeError(probe.error or "GPT-SoVITS 安装不完整")
        python = str(probe.python_path)
        gpt_dir = probe.install_dir / "GPT_SoVITS"
        pretrained = gpt_dir / "pretrained_models"
        env = self._training_env(asset_id)
        dataset = self.dataset_dir(asset_id)
        exp_dir = dataset / "exp"
        asset_out = self.output_dir(asset_id)
        self._merge_preprocess_shards(exp_dir)

        commands: list[tuple[str, list[str], dict]] = [
            (
                "文本预处理",
                [python, str(gpt_dir / "prepare_datasets" / "1-get-text.py")],
                {**env, "bert_pretrained_dir": str(pretrained / "chinese-roberta-wwm-ext-large")},
            ),
            (
                "音频特征提取",
                [python, str(gpt_dir / "prepare_datasets" / "2-get-hubert-wav32k.py")],
                {**env, "cnhubert_base_dir": str(pretrained / "chinese-hubert-base")},
            ),
            (
                "说话人向量",
                [python, str(gpt_dir / "prepare_datasets" / "2-get-sv.py")],
                {**env, "sv_path": str(pretrained / "sv" / "pretrained_eres2netv2w24s4ep4.ckpt")},
            ),
            (
                "语义提取",
                [python, str(gpt_dir / "prepare_datasets" / "3-get-semantic.py")],
                {
                    **env,
                    "pretrained_s2G": str(pretrained / "v2Pro" / "s2Gv2Pro.pth"),
                    "s2config_path": str(gpt_dir / "configs" / "s2v2Pro.json"),
                },
            ),
        ]

        # s1 (GPT) training config
        template = yaml.safe_load(
            (gpt_dir / "configs" / "s1longer-v2.yaml").read_text(encoding="utf-8")
        )
        template["train_semantic_path"] = str(exp_dir / "6-name2semantic.tsv")
        template["train_phoneme_path"] = str(exp_dir / "2-name2text.txt")
        template["output_dir"] = str(asset_out / "s1")
        template["pretrained_s1"] = str(pretrained / "s1v3.ckpt")
        template["train"]["exp_name"] = asset_id
        template["train"]["if_dpo"] = False
        template["train"]["if_save_latest"] = True
        template["train"]["if_save_every_weights"] = True
        template["train"]["half_weights_save_dir"] = str(asset_out / "s1" / "half")
        template["data"]["num_workers"] = 0
        s1_yaml = asset_out / "s1.yaml"
        s1_yaml.parent.mkdir(parents=True, exist_ok=True)
        (asset_out / "s1" / "half").mkdir(parents=True, exist_ok=True)
        s1_yaml.write_text(yaml.safe_dump(template, allow_unicode=True), encoding="utf-8")
        commands.append(("训练 GPT 模型", [python, str(gpt_dir / "s1_train.py"), "-c", str(s1_yaml)], env))

        # s2 (SoVITS) training config
        s2_template = json.loads(
            (gpt_dir / "configs" / "s2v2Pro.json").read_text(encoding="utf-8")
        )
        s2_template["data"]["exp_dir"] = str(exp_dir)
        s2_template["model"]["version"] = "v2Pro"
        s2_template["s2_ckpt_dir"] = str(asset_out / "s2")
        # 8 GB VRAM: keep the SoVITS batch small and use gradient checkpointing.
        s2_template["train"]["batch_size"] = min(s2_template["train"]["batch_size"], 4)
        s2_template["train"]["grad_ckpt"] = True
        s2_template["train"]["epochs"] = 100
        s2_template["train"]["if_save_latest"] = True
        s2_template["train"]["if_save_every_weights"] = True
        s2_template["train"]["save_every_epoch"] = 10
        s2_template["train"]["gpu_numbers"] = "0"
        s2_template["train"]["pretrained_s2G"] = str(pretrained / "v2Pro" / "s2Gv2Pro.pth")
        s2_template["train"]["pretrained_s2D"] = str(pretrained / "v2Pro" / "s2Dv2Pro.pth")
        s2_template["save_weight_dir"] = str(asset_out / "s2" / "weights")
        s2_template["name"] = asset_id
        s2_template["version"] = "v2Pro"
        s2_json = asset_out / "s2.json"
        (asset_out / "s2").mkdir(parents=True, exist_ok=True)
        (asset_out / "s2" / "weights").mkdir(parents=True, exist_ok=True)
        # s2_train.py hard-codes its checkpoint dir as {exp_dir}/logs_s2_<version>.
        (exp_dir / f"logs_s2_{s2_template['model']['version']}").mkdir(parents=True, exist_ok=True)
        s2_json.write_text(json.dumps(s2_template, ensure_ascii=False, indent=2), encoding="utf-8")
        commands.append(("训练 SoVITS 模型", [python, str(gpt_dir / "s2_train.py"), "-c", str(s2_json)], env))
        return commands

    def _merge_preprocess_shards(self, exp_dir: Path) -> None:
        """The distribution's preprocess scripts write sharded outputs named
        ``<base>-<i_part>.<ext>``; training reads the unsharded name. Concatenate
        the shards in order so a single-part run also produces the plain file."""

        for base, suffix in (("2-name2text", ".txt"), ("6-name2semantic", ".tsv")):
            target = exp_dir / f"{base}{suffix}"
            if target.is_file():
                continue
            shards = sorted(exp_dir.glob(f"{base}-*{suffix}"))
            if not shards:
                continue
            with target.open("w", encoding="utf-8") as out:
                for shard in shards:
                    out.write(shard.read_text(encoding="utf-8"))

    def _run_command(self, asset_id: str, command: list[str], env: dict | None = None) -> None:
        probe = self.config.probe()
        if not probe.ok:
            raise RuntimeError(probe.error)
        import os

        merged_env = os.environ.copy()
        merged_env.update(env or {})
        log_path = self.asset_dir(asset_id) / "training.log"
        with log_path.open("a", encoding="utf-8", errors="replace") as log:
            log.write(f"\n>>> {' '.join(command)}\n")
            log.flush()
            subprocess.run(
                command,
                cwd=str(probe.install_dir),
                stdout=log,
                stderr=subprocess.STDOUT,
                env=merged_env,
                check=True,
            )

    def _collect_outputs(self, asset_id: str) -> None:
        """Copy the newest GPT/SoVITS weights into the asset directory."""

        asset_dir = self.asset_dir(asset_id)
        asset_out = self.output_dir(asset_id)

        def newest(directory: Path, suffixes: tuple[str, ...], prefix: str = ""):
            files = [
                p for p in directory.rglob(f"{prefix}*")
                if p.is_file() and p.suffix in suffixes
            ]
            return max(files, key=lambda p: p.stat().st_mtime) if files else None

        # The full-precision Lightning checkpoint includes optimizer state;
        # inference needs the half-precision model weights saved per epoch.
        gpt = newest(asset_out / "s1" / "half", (".ckpt",)) or newest(asset_out / "s1" / "ckpt", (".ckpt",))
        s2_ckpt_dir = self.dataset_dir(asset_id) / "exp" / "logs_s2_v2Pro"
        # The generator (G_) is the inference weight; D_ is the discriminator.
        # savee writes the final inference weight (weight + config) to
        # save_weight_dir; the logs_s2_v2Pro G_ files lack the config key.
        sovits = newest(asset_out / "s2" / "weights", (".pth",)) or newest(
            s2_ckpt_dir, (".pth",), "G_"
        )
        if gpt is None or sovits is None:
            raise RuntimeError("训练完成但未找到模型输出")
        gpt_target = asset_dir / "gpt.ckpt"
        sovits_target = asset_dir / "sovits.pth"
        shutil.copy2(gpt, gpt_target)
        shutil.copy2(sovits, sovits_target)

        session_factory = self._db_session()
        from app.models import VoiceAsset

        with session_factory() as session:
            asset = session.get(VoiceAsset, asset_id)
            if asset is None:
                return
            asset.gpt_weights_path = str(gpt_target)
            asset.sovits_weights_path = str(sovits_target)
            asset.dataset_dir = str(self.dataset_dir(asset_id))
            reference = self._pick_reference(self.dataset_dir(asset_id))
            if reference is not None:
                asset.refer_audio_path = str(reference)
            session.commit()

    def _pick_reference(self, dataset_dir: Path) -> Path | None:
        """Choose a 3-10s clean segment as the few-shot reference audio."""

        import wave as wave_module

        for path in sorted(dataset_dir.glob("*.wav")):
            try:
                with wave_module.open(str(path), "rb") as source:
                    seconds = source.getnframes() / source.getframerate()
            except Exception:
                continue
            if 3.0 <= seconds <= 10.0:
                return path
        return None

    def _update_asset(
        self,
        asset_id: str,
        status: str,
        stage: str,
        error: str | None = None,
    ) -> None:
        session_factory = self._db_session()
        from app.models import VoiceAsset

        with session_factory() as session:
            asset = session.get(VoiceAsset, asset_id)
            if asset is None:
                return
            asset.status = status
            asset.training_stage = stage
            asset.error_message = error
            session.commit()

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "active_asset_id": self._active_asset_id,
            "assets_root": str(self.assets_root),
        }
