"""Рендер списка блоков (из content_extractor.extract_blocks) в Markdown."""


def blocks_to_markdown(page_title: str, page_url: str, blocks: list) -> str:
    lines = [f"# {page_title}", f"_{page_url}_", ""]
    for b in blocks:
        if b["type"] == "heading":
            hashes = "#" * min(int(b["level"][1]) + 1, 6)
            lines.append(f"{hashes} {b['text']}")
        elif b["type"] == "paragraph":
            lines.append(b["text"])
        elif b["type"] == "list":
            for item in b["items"]:
                lines.append(f"- {item}")
        elif b["type"] == "file":
            meta_str = f" — _{b['meta']}_" if b.get("meta") else ""
            lines.append(f"📎 [{b['link_text']}]({b['file_url']}) ({b['extension']}){meta_str}")
        elif b["type"] == "child_pages":
            lines.append("**Подстраницы раздела:**")
            for item in b["items"]:
                lines.append(f"- [{item['title']}]({item['url']})")
        elif b["type"] == "table":
            if b["headers"]:
                lines.append("| " + " | ".join(b["headers"]) + " |")
                lines.append("|" + "|".join(["---"] * len(b["headers"])) + "|")
            for row in b["rows"]:
                if row.get("span_all"):
                    lines.append(f"**{row['text']}**")
                else:
                    lines.append("| " + " | ".join(c.replace("|", "\\|") for c in row["cells"]) + " |")
        lines.append("")
    return "\n".join(lines)
