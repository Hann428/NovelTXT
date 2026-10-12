"""Conservative HTML-to-text conversion. Never rewrite prose."""
import re

from bs4 import BeautifulSoup, NavigableString, Comment

BLOCKS = {"p", "div", "section", "article", "blockquote", "li"}


def preserve_text(node, remove_selector=""):
    copy = BeautifulSoup(str(node), "html.parser")
    selectors = "script,style,noscript,iframe,nav,footer,button,select,input,h1,h2,[hidden],[aria-hidden='true'],.chapter-page-info"
    if remove_selector:
        selectors += "," + remove_selector
    for element in reversed(copy.select(selectors)):
        element.decompose()
    for element in reversed(copy.select("[style]")):
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", element.get("style", ""), re.I):
            element.decompose()
    for element in copy.select("a"):
        if element.get_text(strip=True) in {"上一章", "下一章", "目录", "下一页", "上一页", "加入书架", "报错"}:
            element.decompose()

    def walk(item, pre=False):
        if isinstance(item, Comment):
            return ""
        if isinstance(item, NavigableString):
            value = str(item).replace("\r\n", "\n").replace("\r", "\n")
            if pre:
                return value
            # Source indentation between tags is not a paragraph in the browser.
            if not value.strip(" \t\n") and "\n" in value:
                return ""
            if value.startswith(("\n", " \n", "\t\n")):
                value = value.lstrip(" \t\n")
            if value.rstrip(" \t").endswith("\n"):
                value = value.rstrip(" \t\n")
            return re.sub(r"[\t\n ]+", " ", value)
        if item.name == "br":
            return "\n"
        content = "".join(walk(child, pre or item.name == "pre") for child in item.children)
        if item.name == "pre":
            return content + "\n"
        if item.name in BLOCKS:
            # Keep explicit empty paragraphs and blank lines; do not collapse them.
            if not content:
                return "\n"
            style = item.get("style", "")
            indent = re.search(r"text-indent\s*:\s*([\d.]+)(em|px)", style)
            if indent and not content.startswith((" ", "\u3000", "\xa0", "\n")):
                amount = float(indent[1])
                if (indent[2] == "em" and amount >= 1) or (indent[2] == "px" and amount >= 12):
                    content = "\u3000\u3000" + content
            return content if content.endswith("\n") else content + "\n"
        return content

    # Remove container indentation only. Inner whitespace, including empty lines, stays.
    result = walk(copy)
    return result.strip("\r\n")
