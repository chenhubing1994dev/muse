"""Create a researched vertical short using verified local Wan shots."""
from __future__ import annotations
import hashlib, json, math, struct, subprocess, wave, zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from muse.media import discover_media_tools
from muse.progress import new_evolution_cycle_id, record_evolution_cycle_event
from muse.research_knowledge import ResearchKnowledgeStore
from muse.video_product_builder import VideoProductBuilder

SCENES = [
 { "id":"shot-01","file":"shot-01.mp4","duration":2.083333,"caption":"你敢信 AI 的每一句话？","prompt_id":None,"seed":None}, { "id":"shot-02","file":"shot-02.mp4","duration":2.083333,"caption":"流畅表达\n不等于真实。","prompt_id":None,"seed":None},
 { "id":"shot-03","file":"shot-03.mp4","duration":2.083333,"caption":"① 拆开主张\n人名、日期、数字、引文\n逐条核验。","prompt_id":None,"seed":None},
 { "id":"shot-04","file":"shot-04.mp4","duration":2.083333,"caption":"② 查原始来源\n链接存在？原文支持？","prompt_id":"cf75de4e-c4ee-4b32-a39a-12cf9ddc8ab6","seed":20261114},
 { "id":"shot-05","file":"shot-05.mp4","duration":5.083333,"caption":"③ 主动找反证\n什么证据会推翻它？","prompt_id":"5863beb5-122d-45c1-b32e-1dcd8dab2999","seed":20261112},
 { "id":"shot-06","file":"shot-06.mp4","duration":5.083333,"caption":"找不到来源\n就标记：未证实。","prompt_id":"6bd3aff6-ad05-46cb-bc2b-ef92f19a81f0","seed":20261113},]
NIST_URL = "https://doi.org/10.6028/NIST.AI.600-1"
NIST_PDF = "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf"
NIST_TITLE = "Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile"
TITLE = "别把 AI 的自信，当成事实｜三步查证法"

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def run(command: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown process error")[-3000:]
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")
    return result

def write_ambient(path: Path, seconds: float, rate: int = 48000) -> None:
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2); output.setsampwidth(2); output.setframerate(rate)
        block = bytearray()
        for index in range(frames):
            t = index / rate
            attack = min(1.0, t / 0.7)
            release = min(1.0, max(0.0, (seconds - t) / 1.0))
            pulse = 0.84 + 0.16 * math.sin(2 * math.pi * 0.16 * t)
            chord = (0.22 * math.sin(2 * math.pi * 55.0 * t)
                     + 0.14 * math.sin(2 * math.pi * 82.41 * t)
                     + 0.10 * math.sin(2 * math.pi * 110.0 * t)
                     + 0.055 * math.sin(2 * math.pi * 164.81 * t)
                     + 0.025 * math.sin(2 * math.pi * 329.63 * t))
            shimmer = 0.018 * math.sin(2 * math.pi * (440.0 + 5.0 * math.sin(t * 0.7)) * t)
            sample = int(max(-0.8, min(0.8, (chord + shimmer) * attack * release * pulse * 0.30)) * 32767)
            block.extend(struct.pack("<hh", sample, sample))
            if len(block) >= 65536:
                output.writeframesraw(block); block.clear()
        if block: output.writeframesraw(block)

def srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{milliseconds:03d}"

def build(workspace: Path, output_dir: Path) -> dict[str, Any]:
    workspace, output_dir = workspace.resolve(), output_dir.resolve()
    if output_dir.exists(): raise FileExistsError(f"refusing to overwrite existing output directory: {output_dir}")
    source_dir = workspace / "data" / "video_sources" / "muse-cosmos-film"
    sources = [source_dir / item["file"] for item in SCENES]
    if not source_dir.is_dir() or any(not path.is_file() or path.is_symlink() for path in sources):
        raise FileNotFoundError("one or more verified Muse Cosmos source shots are missing or unsafe")
    shot4_meta_path = source_dir / "shot-04.json"
    if not shot4_meta_path.is_file() or shot4_meta_path.is_symlink():
        raise FileNotFoundError("verified shot-04 generation metadata is missing")
    shot4_meta = json.loads(shot4_meta_path.read_text(encoding="utf-8"))
    shot4_generation = shot4_meta.get("generation", {})
    if (shot4_generation.get("status") != "completed"
        or shot4_meta.get("sha256") != sha256(source_dir / "shot-04.mp4")
        or shot4_generation.get("prompt_id") != "cf75de4e-c4ee-4b32-a39a-12cf9ddc8ab6"
        or shot4_generation.get("seed") != 20261114):
        raise RuntimeError("shot-04 generation metadata does not match its source MP4")
    inventory = discover_media_tools()
    tools = {item.tool_id: item.executable for item in inventory.tools}
    ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
    if not ffmpeg or not ffprobe: raise RuntimeError("FFmpeg and FFprobe must both be installed")
    builder = VideoProductBuilder(workspace)
    prior = builder.verify_path(workspace / "data" / "products",
        "59148fb9f5299ad0292157ed38194996", "f0140e0b32facf564f95")
    if not prior.get("validated") or prior.get("mp4_sha256") != "9423aca20297fd028721b727867ab74edd110c1a6dd4e660d478a375633c6a47":
        raise RuntimeError("existing six-shot Wan rough cut failed its stored integrity check")
    cycle_id = new_evolution_cycle_id()
    progress_db = workspace / "data" / "tasks" / "muse.sqlite3"
    record_evolution_cycle_event(progress_db, cycle_id, "started",
        summary="Research-backed three-step AI answer verification short video started.")
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        source_facts = []
        for scene, path in zip(SCENES, sources):
            probe = json.loads(run([ffprobe,"-v","error","-show_entries",
                "format=duration:stream=codec_name,codec_type,width,height,avg_frame_rate",
                "-of","json",str(path)],workspace,timeout=30).stdout)
            video = next((item for item in probe.get("streams",[]) if item.get("codec_type")=="video"),None)
            duration = float(probe.get("format",{}).get("duration",0))
            if (not video or video.get("codec_name")!="h264" or video.get("width")!=288
                or video.get("height")!=512 or video.get("avg_frame_rate")!="12/1"
                or abs(duration-scene["duration"])>0.15):
                raise RuntimeError(f"source shot failed media verification: {path.name}")
            source_facts.append({"file":path.name,"sha256":sha256(path),"bytes":path.stat().st_size,
                "duration_seconds":duration,"codec":"h264","width":288,"height":512,"fps":12,
                "model":"Wan 2.1 T2V 1.3B","prompt_id":scene["prompt_id"],"seed":scene["seed"],
                "prompt_metadata":"retained" if scene["id"]=="shot-04" else "not retained in source folder"})

        duration = 3.0 + sum(item["duration"] for item in SCENES) + 3.0
        audio_path = output_dir / "ambient-original.wav"
        write_ambient(audio_path,duration)
        srt_path = output_dir / "subtitles.srt"
        cues = [(0.0,3.0,"AI说得像真的\n就一定是真的吗？")]
        cursor = 3.0
        for scene in SCENES:
            cues.append((cursor,cursor+scene["duration"],scene["caption"]))
            cursor += scene["duration"]
        cues.append((cursor,cursor+3.0,"AI帮你加速\n证据替你把关\n依据：NIST AI 600-1（2024）"))
        srt_text = "\n\n".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{caption}"
            for i,(a,b,caption) in enumerate(cues,start=1)) + "\n"
        srt_path.write_text(srt_text,encoding="utf-8-sig")
        caption_files = []
        for index,(_,_,caption) in enumerate(cues):
            caption_path = output_dir / f"caption-{index:02d}.txt"
            caption_path.write_text(caption,encoding="utf-8")
            caption_files.append(caption_path)

        final_path = output_dir / "ai-verification-three-steps-9x16.mp4"
        filters = ["[0:v]format=yuv420p,setpts=PTS-STARTPTS[v0]"]
        for index in range(1,len(SCENES)+1):
            filters.append(f"[{index}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,setsar=1,fps=24,eq=contrast=1.06:saturation=1.08:"
                f"brightness=-0.025,format=yuv420p,setpts=PTS-STARTPTS[v{index}]")
        outro_index = len(SCENES)+1
        filters.append(f"[{outro_index}:v]format=yuv420p,setpts=PTS-STARTPTS[v{outro_index}]")
        labels = "".join(f"[v{i}]" for i in range(outro_index+1))
        filters.append(f"{labels}concat=n={outro_index+1}:v=1:a=0[vcat]")
        font_path = "C\\:/Windows/Fonts/msyh.ttc"
        current_label = "vcat"
        for index,(start,end,_) in enumerate(cues):
            next_label = f"captioned{index}"
            caption_relative = caption_files[index].relative_to(workspace).as_posix()
            filters.append(
                f"[{current_label}]drawtext=fontfile='{font_path}':textfile='{caption_relative}':"
                f"fontcolor=white:fontsize=62:line_spacing=18:borderw=2:bordercolor=black:"
                f"box=1:boxcolor=0x07111e@0.82:boxborderw=24:"
                f"x=(w-text_w)/2:y=h*0.70-text_h/2:"
                f"enable='between(t,{start+0.12:.3f},{end:.3f})'[{next_label}]"
            )
            current_label = next_label
        filters.append(f"[{current_label}]fade=t=in:st=0:d=0.25,"
            f"fade=t=out:st={duration-0.25:.3f}:d=0.25[v]")
        command = [ffmpeg,"-hide_banner","-y","-f","lavfi","-i",
            "color=c=0x07111e:s=1080x1920:r=24:d=3"]
        for path in sources: command.extend(["-i",str(path)])
        command.extend(["-f","lavfi","-i","color=c=0x07111e:s=1080x1920:r=24:d=3",
            "-i",str(audio_path),"-filter_complex",";".join(filters),
            "-map","[v]","-map",f"{outro_index+1}:a:0"])
        encoder = "h264_amf" if "h264_amf" in set(inventory.hardware_encoders) else "libx264"
        if encoder=="h264_amf": command.extend(["-c:v","h264_amf","-quality","quality","-b:v","8M"])
        else: command.extend(["-c:v","libx264","-preset","fast","-crf","19"])
        command.extend(["-pix_fmt","yuv420p","-r","24","-c:a","aac","-b:a","160k",
            "-ar","48000","-ac","2","-movflags","+faststart",str(final_path)])
        try: run(command,workspace,timeout=900)
        except RuntimeError:
            if encoder!="h264_amf": raise
            final_path.unlink(missing_ok=True)
            fallback=command.copy(); index=fallback.index("h264_amf")
            del fallback[index:index+3]
            fallback[index:index]=["libx264","-preset","fast","-crf","19"]
            run(fallback,workspace,timeout=1200); encoder="libx264"

        probe = json.loads(run([ffprobe,"-v","error","-show_entries",
            "format=duration,size:stream=codec_name,codec_type,width,height,avg_frame_rate,channels,sample_rate",
            "-of","json",str(final_path)],workspace,timeout=30).stdout)
        streams=probe.get("streams",[])
        video=next((x for x in streams if x.get("codec_type")=="video"),None)
        audio=next((x for x in streams if x.get("codec_type")=="audio"),None)
        actual_duration=float(probe.get("format",{}).get("duration",0))
        if (not video or not audio or video.get("codec_name")!="h264"
            or video.get("width")!=1080 or video.get("height")!=1920
            or video.get("avg_frame_rate")!="24/1" or audio.get("codec_name")!="aac"
            or audio.get("sample_rate")!="48000" or audio.get("channels")!=2
            or abs(actual_duration-duration)>0.3):
            raise RuntimeError(f"final MP4 failed technical acceptance: {probe}")
        run([ffmpeg,"-hide_banner","-v","error","-i",str(final_path),"-f","null","-"],workspace,timeout=600)
        cover=output_dir/"cover.jpg"
        run([ffmpeg,"-hide_banner","-y","-ss","6.5","-i",str(final_path),
             "-frames:v","1","-q:v","2",str(cover)],workspace,timeout=60)

        with ResearchKnowledgeStore(workspace/"data"/"research"/"knowledge.sqlite3") as knowledge:
            source_node=knowledge.upsert_node("source","NIST AI 600-1 — Generative AI Profile",
                summary=("NIST official guidance published 2024-07-26. Section 2.2 describes "
                    "confabulation as confidently presented erroneous or false content; "
                    "MS-2.5-003 recommends reviewing and verifying generated sources and citations."),
                external_ref=f"muse-video-research:{cycle_id}:nist-ai-600-1",
                metadata={"evolution_cycle_id":cycle_id,"source_url":NIST_PDF,"doi":NIST_URL,
                    "published_at":"2024-07-26","sections":["2.2","MS-2.5-003"],
                    "evidence_quality":"high","source_independence":"unassessed",
                    "source_kind":"official_standard_guidance"},provenance=(NIST_PDF,NIST_URL))
            claim_node=knowledge.upsert_node("claim",
                "生成式 AI 可能自信地输出错误内容，亦可能产生误导性引用",
                summary=("Scoped to NIST AI 600-1 §2.2; recorded as a proposed claim pending independent review, "
                    "not as a universal error-rate statement."),
                external_ref=f"muse-video-research:{cycle_id}:confabulation-claim",
                metadata={"evolution_cycle_id":cycle_id,"evidence_status":"source_supported_pending_independent_review",
                    "source_node_id":source_node["id"],"claim_scope":"NIST AI 600-1 §2.2"},
                provenance=(NIST_PDF,))
            idea_node=knowledge.upsert_node("idea","AI 答案三步查证法：拆主张、查原文、找反证",
                summary=("Practical educational checklist inspired by NIST source-verification guidance. "
                    "The counter-evidence step is editorial advice, not a direct NIST quotation."),
                external_ref=f"muse-video-research:{cycle_id}:three-step-check",
                metadata={"evolution_cycle_id":cycle_id,"idea_type":"educational_short_video",
                    "source_node_id":source_node["id"],"claim_node_id":claim_node["id"]},
                provenance=(NIST_PDF,))
        record_evolution_cycle_event(progress_db,cycle_id,"progress",
            summary="NIST source and proposed claim linked to a three-step educational video; six verified Wan shots reused.")

        scene_records=[]
        for index,scene in enumerate(SCENES,start=1):
            prompt=(shot4_meta["prompt"] if scene["id"]=="shot-04" else
                f"Existing Wan 2.1 T2V source shot {index:02d}; original prompt text not retained in this source folder.")
            scene_records.append({"order":index,"prompt":prompt,"prompt_id":scene["prompt_id"],
                "seed":scene["seed"],"generation_status":"generated","source_file":scene["file"]})
        product=builder.package_generated_clip(TITLE,
            "A concise Chinese educational short: decompose AI claims, check original sources, and actively look for counter-evidence.",
            final_path,generation_metadata={"status":"completed",
                "model":"Wan 2.1 T2V 1.3B (six existing AI shots) + FFmpeg editorial assembly",
                "aspect_ratio":"9:16","fps":24,"duration_seconds":round(actual_duration,3),
                "frames":round(actual_duration*24),"width":1080,"height":1920,"bytes":final_path.stat().st_size},
            scene_records=scene_records,evolution_cycle_id=cycle_id)
        verified=builder.verify_path(workspace/"data"/"products",product["initiative_id"],product["version_id"])
        if not verified.get("validated") or verified.get("mp4_sha256")!=sha256(final_path):
            raise RuntimeError("VideoProductBuilder final integrity verification failed")

        storyboard={"schema_version":1,"title":TITLE,"production_mode":"local_wan21_ai_footage_plus_ffmpeg_edit",
            "evolution_cycle_id":cycle_id,"duration_seconds":round(actual_duration,3),
            "aspect_ratio":"9:16","fps":24,"width":1080,"height":1920,
            "voiceover":"not used; no local Chinese TTS voice was available",
            "audio":"original synthesized ambient bed; no third-party music",
            "research":{"source":NIST_TITLE,"published_at":"2024-07-26","doi":NIST_URL,"pdf":NIST_PDF,
                "scope":"NIST AI 600-1 §2.2 and suggested action MS-2.5-003",
                "editorial_method":"The three-step checklist is practical editorial advice; counter-evidence is not a direct NIST quotation."},
            "scenes":[{"order":i,"id":scene["id"],"source_file":scene["file"],
                "source_sha256":source_facts[i-1]["sha256"],"source_duration_seconds":source_facts[i-1]["duration_seconds"],
                "caption":scene["caption"],"prompt":shot4_meta["prompt"] if scene["id"]=="shot-04" else None,
                "prompt_id":scene["prompt_id"],"seed":scene["seed"],
                "generation_status":"existing_wan21_ai_source"} for i,scene in enumerate(SCENES,start=1)],
            "source_assets":source_facts}
        storyboard_path=output_dir/"storyboard.json"
        storyboard_path.write_text(json.dumps(storyboard,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        source_notes={"title":NIST_TITLE,"publisher":"National Institute of Standards and Technology",
            "published_at":"2024-07-26","doi":NIST_URL,"pdf":NIST_PDF,
            "relevant_sections":{"2.2":"Defines confabulation as confidently presented erroneous or false generative-AI content and notes generated logic or citations can mislead.",
                "MS-2.5-003":"Recommends reviewing and verifying sources and citations in generative-AI outputs."},
            "editorial_boundary":"NIST guidance does not supply a universal hallucination rate or certify this checklist. Counter-evidence is editorial advice."}
        notes_path=output_dir/"source-notes.json"
        notes_path.write_text(json.dumps(source_notes,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        readme=(f"# {TITLE}\n\n一条面向普通用户的 AI 信息核验短片：拆开可核验主张、打开原始来源、主动寻找反证。\n\n"
            f"- 时长：{actual_duration:.3f} 秒；竖屏 1080×1920；24 FPS；H.264 + AAC stereo。\n"
            "- 画面：复用六段此前已实机生成并验证的本地 Wan 2.1 T2V 1.3B 镜头；本轮没有重新启动 ComfyUI 或消耗 GPU 生成时间。\n"
            "- 后期：FFmpeg 画幅统一、字幕烧录、淡入淡出、原创合成氛围音轨。\n"
            "- 旁白：未使用；Runner 检查未发现可用的本地中文 TTS 语音/模型，没有伪称真人旁白。\n"
            f"- 研究依据：NIST AI 600-1（2024-07-26），§2.2 与 MS-2.5-003；详见 source-notes.json。\n"
            "- 内容边界：三步查证法是实用编辑建议，不是 NIST 原文逐字结论。\n"
            "- 产品状态：testing；正式发布前仍需人工审片。未上传或自动发布到社交平台。\n")
        readme_path=output_dir/"README.md"
        readme_path.write_text(readme,encoding="utf-8")
        product_dir=workspace/product["relative_path"]
        bundle=output_dir/"delivery-bundle.zip"
        bundle_files=[(final_path,"video-9x16.mp4"),(srt_path,"subtitles.srt"),(cover,"cover.jpg"),
            (storyboard_path,"storyboard.json"),(notes_path,"source-notes.json"),(audio_path,"ambient-original.wav"),
            (readme_path,"README.md"),(product_dir/"product.zip","muse-product.zip"),
            (product_dir/"manifest.json","muse-product-manifest.json")]
        with zipfile.ZipFile(bundle,"w",compression=zipfile.ZIP_DEFLATED) as archive:
            for source,name in bundle_files: archive.write(source,name)
        with zipfile.ZipFile(bundle) as archive:
            if archive.testzip() is not None or len(archive.namelist())!=len(bundle_files):
                raise RuntimeError("delivery bundle ZIP validation failed")
        result={"status":"completed","title":TITLE,"evolution_cycle_id":cycle_id,
            "mp4":{"path":str(final_path.relative_to(workspace)).replace("\\","/"),
                "bytes":final_path.stat().st_size,"sha256":sha256(final_path),
                "duration_seconds":round(actual_duration,3),"width":1080,"height":1920,"fps":24,
                "codec":"h264","audio_codec":"aac","audio_sample_rate":48000,"encoder":encoder,"full_decode":"passed"},
            "cover":{"path":str(cover.relative_to(workspace)).replace("\\","/"),"sha256":sha256(cover)},
            "product":product,"product_validation":verified,
            "research_nodes":{"source_node_id":source_node["id"],"claim_node_id":claim_node["id"],
                "idea_node_id":idea_node["id"],"knowledge_linkage":product.get("knowledge_linkage")},
            "bundle":{"path":str(bundle.relative_to(workspace)).replace("\\","/"),
                "bytes":bundle.stat().st_size,"sha256":sha256(bundle),"entries":len(bundle_files),"crc_valid":True},
            "voiceover":"not used; no local Chinese TTS voice available",
            "status_note":"testing; human editorial review required before publication",
            "completed_at":datetime.now(timezone.utc).isoformat()}
        (output_dir/"generation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        record_evolution_cycle_event(progress_db,cycle_id,"completed",
            summary="Research-backed 9:16 short rendered, fully decoded, packaged and linked to Creator Initiative/Research Wiki.")
        return result
    except Exception:
        record_evolution_cycle_event(progress_db,cycle_id,"failed",summary="High-value short production failed.",
            error_code="video.high_value_short_failed")
        raise

def main() -> int:
    workspace=Path(__file__).resolve().parents[1]
    output_dir=workspace/"data"/"prometheus"/"high-value-short-v2"
    try:
        print(json.dumps(build(workspace,output_dir),ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status":"failed","error_type":type(exc).__name__,"message":str(exc)[:1200]},
            ensure_ascii=False,indent=2))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
