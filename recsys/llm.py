"""Optional local LLM layer (Qwen2.5-Instruct on the GPU).

Loaded lazily — only when a query uses --parse, --rerank, or --explain — so the
base embedding+tags path never pays the model-load cost. Every method degrades
gracefully: on a load or parse failure it returns a no-op result so the
recommender still works.
"""
from __future__ import annotations

import json
import re

from recsys.search import Filters, Result

DEFAULT_LLM = "Qwen/Qwen2.5-3B-Instruct"

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _first_json(text: str) -> dict | None:
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _snippet(rec, n: int = 200) -> str:
    return rec.synopsis[:n].replace("\n", " ")


class LocalLLM:
    def __init__(self, model_name: str = DEFAULT_LLM, device: str = "auto") -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        dev = ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
        self.model_name = model_name
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name, torch_dtype="auto", device_map=dev
        )
        self.device = dev

    def _chat(self, system: str, user: str, max_new_tokens: int = 256) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)
        generated = self.model.generate(
            **inputs, max_new_tokens=max_new_tokens, do_sample=False
        )
        out = generated[0][inputs.input_ids.shape[1]:]
        return self.tokenizer.decode(out, skip_special_tokens=True).strip()

    # ── Query understanding ─────────────────────────────────────────────────
    def parse_query(self, text: str) -> tuple[str, set[str], Filters]:
        """Turn a free-text request into (semantic_text, tags, Filters).

        Falls back to (text, {}, empty Filters) if the model output can't be parsed.
        """
        system = (
            "你是一个中文百合(GL)小说检索助手。把用户的口语化需求拆解为 JSON，"
            "字段：semantic_text(用于语义检索的简洁中文描述), "
            "tags(内容标签数组，如 破镜重圆/ABO/校园/先婚后爱), "
            "status(完结 或 连载 或 null), min_chapters(整数或null), "
            "year_from(年份或null), year_to(年份或null)。只输出 JSON。"
        )
        try:
            raw = self._chat(system, text, max_new_tokens=256)
        except Exception:
            return text, set(), Filters()
        data = _first_json(raw)
        if not data:
            return text, set(), Filters()
        semantic = str(data.get("semantic_text") or text)
        tags = {str(t) for t in (data.get("tags") or []) if t}
        status = data.get("status")
        f = Filters(
            status=status if status in ("完结", "连载") else None,
            min_chapters=data.get("min_chapters") if isinstance(data.get("min_chapters"), int) else None,
            year_from=data.get("year_from") if isinstance(data.get("year_from"), int) else None,
            year_to=data.get("year_to") if isinstance(data.get("year_to"), int) else None,
        )
        return semantic, tags, f

    # ── Listwise rerank ─────────────────────────────────────────────────────
    def rerank(self, query: str, results: list[Result], top_k: int = 20) -> list[Result]:
        """Reorder the top_k candidates by reading their synopses; annotates
        each surviving result with a one-line reason. Unparseable → unchanged."""
        cand = results[:top_k]
        listing = "\n".join(
            f"[{i}] 《{r.record.title}》 标签:{'/'.join(r.record.tags) or '无'} 简介:{_snippet(r.record, 140)}"
            for i, r in enumerate(cand)
        )
        system = (
            "你是百合小说推荐助手。根据用户需求，对候选小说按契合度从高到低重排，"
            "并为每本写一句简短中文推荐理由。只输出 JSON："
            '{"order":[{"i":候选编号,"reason":"理由"}, ...]}。'
        )
        user = f"用户需求：{query}\n\n候选：\n{listing}"
        try:
            raw = self._chat(system, user, max_new_tokens=700)
        except Exception:
            return results
        data = _first_json(raw)
        if not data or "order" not in data:
            return results
        reranked: list[Result] = []
        seen: set[int] = set()
        for item in data["order"]:
            try:
                i = int(item["i"])
            except (KeyError, ValueError, TypeError):
                continue
            if 0 <= i < len(cand) and i not in seen:
                seen.add(i)
                res = cand[i]
                res.reason = str(item.get("reason", "")).strip()
                reranked.append(res)
        # Append any candidates the model dropped, then the untouched tail.
        reranked.extend(cand[i] for i in range(len(cand)) if i not in seen)
        reranked.extend(results[top_k:])
        return reranked

    # ── Explanation ─────────────────────────────────────────────────────────
    def explain(self, query: str, rec) -> str:
        system = "用一句不超过30字的中文说明这本百合小说为什么符合用户口味。只输出这句话。"
        user = f"需求：{query}\n小说：《{rec.title}》 标签:{'/'.join(rec.tags) or '无'} 简介:{_snippet(rec, 160)}"
        try:
            return self._chat(system, user, max_new_tokens=60).strip()
        except Exception:
            return ""
