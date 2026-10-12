from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

import soupsieve

SELECTOR_KEYS = {
    "toc_selector", "toc_next_selector", "content_selector", "title_selector",
    "next_page_selector", "next_chapter_selector", "remove_selector",
}


@cache
def _config_dir():
    bundled = Path(__file__).resolve().parent / "config"
    if bundled.is_dir():
        return bundled
    source = Path(__file__).resolve().parents[2] / "config"
    return source if source.is_dir() else None


@cache
def builtin_config():
    source = _config_dir() / "default.toml" if _config_dir() else None
    resource = source if source and source.is_file() else files("novel_get.config").joinpath("default.toml")
    return tomllib.loads(resource.read_text(encoding="utf-8-sig"))


@cache
def _bundled_site_paths():
    source_sites = _config_dir() / "sites" if _config_dir() else None
    if source_sites and source_sites.is_dir():
        return sorted(source_sites.glob("*.toml"))
    try:
        return sorted(files("novel_get.config").joinpath("sites").glob("*.toml"))
    except (TypeError, OSError):
        return []


def selectors(value, location):
    if not isinstance(value, dict):
        raise ValueError(f"{location}: 选择器配置必须是键值表")
    unknown = value.keys() - SELECTOR_KEYS
    if unknown:
        raise ValueError(f"{location}: 未知配置项: {', '.join(sorted(unknown))}")
    for key, selector in value.items():
        if not isinstance(selector, str):
            raise ValueError(f"{location}.{key}: 选择器必须是字符串")
        # Empty strings explicitly restore automatic detection / default cleaning.
        if selector:
            try:
                soupsieve.compile(selector)
            except soupsieve.SelectorSyntaxError as exc:
                raise ValueError(f"{location}.{key}: 无效 CSS 选择器: {exc}") from exc
    return dict(value)


def hostname(value):
    return value.rstrip(".").encode("idna").decode("ascii").lower()


def domain_pattern(value, location):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{location}: 域名必须是非空字符串")
    wildcard = value.startswith("*.")
    try:
        domain = hostname(value[2:] if wildcard else value)
    except UnicodeError as exc:
        raise ValueError(f"{location}: 无效域名 {value!r}: {exc}") from exc
    if len(domain) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in domain.split(".")):
        raise ValueError(f"{location}: 无效域名 {value!r}，仅支持完整主机名或 *.example.com，不含协议、端口和路径")
    return "*." + domain if wildcard else domain


@dataclass
class SiteRule:
    name: str
    domains: list[str]
    selectors: dict[str, str]

    def match(self, host):
        scores = []
        for domain in self.domains:
            if domain.startswith("*."):
                if host.endswith(domain[1:]):
                    scores.append((0, len(domain)))
            elif host == domain:
                scores.append((1, len(domain)))
        return max(scores, default=None)


def site_rules(value, location):
    if not isinstance(value, dict):
        raise ValueError(f"{location}: sites 必须是站点配置表")
    rules = []
    for name, site in value.items():
        label = f"{location}.sites.{name}"
        if not isinstance(site, dict) or not isinstance(site.get("domains"), list) or not site["domains"]:
            raise ValueError(f"{label}: domains 必须是非空域名数组")
        domains = [domain_pattern(domain, label) for domain in site["domains"]]
        rules.append(SiteRule(name, domains, selectors({k: v for k, v in site.items() if k != "domains"}, label)))
    return rules


@dataclass
class SiteConfigs:
    # Each file is a layer. Later files override earlier files field by field.
    layers: list[list[SiteRule]]

    def resolve(self, url):
        host = hostname(urlsplit(url).hostname or "")
        result = {}
        for layer in self.layers:
            matches = [(rule.match(host), index, rule) for index, rule in enumerate(layer)]
            for _, _, rule in sorted((item for item in matches if item[0] is not None), key=lambda item: item[:2]):
                result.update(rule.selectors)
        return result


def load_site_configs(paths=(), directory=None):
    directory = Path("config/sites") if directory is None else Path(directory)
    layers = [site_rules(builtin_config().get("sites", {}), "config/default.toml")]
    for path in _bundled_site_paths():
        text = path.read_text(encoding="utf-8-sig")
        value = tomllib.loads(text)
        if value.keys() - {"sites"}:
            raise ValueError(f"{path}: 自定义 TOML 仅支持 [sites.<名称>] 配置表")
        layers.append(site_rules(value.get("sites", {}), str(path)))
    for path in [*sorted(directory.glob("*.toml")), *paths]:
        path = Path(path)
        if path.suffix.lower() != ".toml":
            raise ValueError(f"{path}: 网站配置仅支持 .toml 文件，请使用 [sites.<名称>] 和 domains 定义站点规则")
        try:
            text = path.read_text(encoding="utf-8-sig")
            value = tomllib.loads(text)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        if value.keys() - {"sites"}:
            raise ValueError(f"{path}: 自定义 TOML 仅支持 [sites.<名称>] 配置表")
        layers.append(site_rules(value.get("sites", {}), str(path)))
    return SiteConfigs(layers)


def resolve_config(config, url):
    if config is None:
        config = load_site_configs()
    return config.resolve(url) if isinstance(config, SiteConfigs) else config
