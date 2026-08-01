"""生成用户注册、认证与租户隔离流程图。

本图按当前代码实现绘制：

* 注册/登录写入 ``user_profiles``，密码使用 Argon2id，返回 24 小时 JWT；
* 每个受保护 API 从 Bearer Token 解析 ``user_id``，再把它传给 Service/Repository；
* 合同、报告和会话查询通过 ``user_id`` 及会话归属检查完成个人空间隔离；
* 当前没有独立 ``tenant_id``、组织成员关系或角色权限，法律 RAG 语料仍是共享知识库。

该图是架构说明，不是新的授权实现。
"""

from html import escape
from pathlib import Path


WIDTH = 1800
HEIGHT = 1220
OUTPUT = Path(__file__).with_name("user-registration-tenant-isolation.svg")


def add(lines: list[str], value: str) -> None:
    lines.append(value)


def text(
    lines: list[str],
    x: float,
    y: float,
    value: str,
    *,
    size: int = 14,
    fill: str = "#111827",
    weight: int = 400,
    anchor: str = "start",
    role: str = "label",
    owner: str | None = None,
) -> None:
    owner_attr = f' data-owner="{owner}"' if owner else ""
    add(
        lines,
        f'<text data-graph-role="{role}"{owner_attr} x="{x}" y="{y}" '
        f'font-size="{size}px" fill="{fill}" font-weight="{weight}" '
        f'text-anchor="{anchor}">{escape(value)}</text>',
    )


def card(
    lines: list[str],
    node_id: str,
    x: int,
    y: int,
    width: int,
    title: str,
    subtitle: str,
    *,
    fill: str,
    stroke: str,
    badge: str,
    badge_fill: str,
    status: str,
    status_fill: str,
    status_text: str,
    height: int = 110,
) -> None:
    bounds = f"{x} {y} {x + width} {y + height}"
    add(
        lines,
        f'<g id="{node_id}" data-node-id="{node_id}" data-graph-role="node" '
        f'data-graph-bounds="{bounds}">',
    )
    add(
        lines,
        f'<rect data-node-id="{node_id}" x="{x}" y="{y}" width="{width}" '
        f'height="{height}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>',
    )
    add(lines, f'<circle data-node-id="{node_id}" cx="{x + 32}" cy="{y + 34}" r="20" fill="{badge_fill}"/>')
    text(lines, x + 32, y + 39, badge, size=10, fill="#ffffff", weight=700, anchor="middle", role="node")
    text(lines, x + 67, y + 31, title, size=15, weight=600, role="node")
    text(lines, x + 67, y + 58, subtitle, size=12, fill="#4b5563", role="node")
    add(
        lines,
        f'<rect data-node-id="{node_id}" x="{x + width - 112}" y="{y + height - 31}" '
        f'width="98" height="21" rx="11" fill="{status_fill}"/>',
    )
    text(
        lines,
        x + width - 63,
        y + height - 16,
        status,
        size=10,
        fill=status_text,
        weight=600,
        anchor="middle",
        role="node",
    )
    add(lines, "</g>")


def decision(
    lines: list[str],
    node_id: str,
    cx: int,
    cy: int,
    half_width: int,
    half_height: int,
    title: str,
    subtitle: str,
) -> None:
    bounds = f"{cx - half_width} {cy - half_height} {cx + half_width} {cy + half_height}"
    add(
        lines,
        f'<g id="{node_id}" data-node-id="{node_id}" data-graph-role="decision" '
        f'data-graph-bounds="{bounds}">',
    )
    add(
        lines,
        f'<polygon data-node-id="{node_id}" points="{cx},{cy - half_height} '
        f'{cx + half_width},{cy} {cx},{cy + half_height} {cx - half_width},{cy}" '
        'fill="#fff7ed" stroke="#ea580c" stroke-width="1.7"/>',
    )
    text(lines, cx, cy - 3, title, size=13, weight=600, anchor="middle", role="node")
    text(lines, cx, cy + 18, subtitle, size=11, fill="#9a3412", anchor="middle", role="node")
    add(lines, "</g>")


def edge(
    lines: list[str],
    edge_id: str,
    path: str,
    *,
    source: str,
    target: str,
    color: str,
    marker: str,
    label: str | None = None,
    label_x: int | None = None,
    label_y: int | None = None,
    dashed: bool = False,
) -> None:
    dash = ' stroke-dasharray="7,5"' if dashed else ""
    add(
        lines,
        f'<path id="{edge_id}" data-edge-id="{edge_id}" data-graph-role="edge" '
        f'data-source="{source}" data-target="{target}" d="{path}" fill="none" '
        f'stroke="{color}" stroke-width="2"{dash} marker-end="url(#{marker})"/>',
    )
    if label:
        text(
            lines,
            label_x or 0,
            label_y or 0,
            label,
            size=11,
            fill=color,
            weight=600,
            anchor="middle",
            owner=edge_id,
        )


def note(lines: list[str], node_id: str, x: int, y: int, width: int, title: str, lines_text: list[str]) -> None:
    height = 112
    bounds = f"{x} {y} {x + width} {y + height}"
    add(
        lines,
        f'<g id="{node_id}" data-node-id="{node_id}" data-graph-role="node" '
        f'data-graph-bounds="{bounds}">',
    )
    add(
        lines,
        f'<rect data-node-id="{node_id}" x="{x}" y="{y}" width="{width}" height="{height}" '
        'rx="12" fill="#fff7ed" stroke="#fb923c" stroke-width="1.6" stroke-dasharray="7,5"/>',
    )
    text(lines, x + 20, y + 30, title, size=15, fill="#9a3412", weight=600, role="node")
    for index, value in enumerate(lines_text):
        text(lines, x + 20, y + 58 + index * 20, value, size=12, fill="#7c2d12", role="node")
    add(lines, "</g>")


def build_svg() -> str:
    lines: list[str] = []
    add(
        lines,
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc" '
        'data-generator="fireworks-tech-graph" data-schema-version="1" data-style-id="1" '
        'data-visual-theme="flat-icon" data-diagram-type="data-flow" data-semantic-profile="generic" '
        'data-quality-profile="standard" data-min-node-gap="40" data-min-container-gutter="20" '
        'data-min-label-clearance="4" data-min-segment-length="16">',
    )
    add(lines, '<title id="title">用户注册与租户隔离流程</title>')
    add(
        lines,
        '<desc id="desc">展示当前系统从注册登录、JWT 身份解析，到 user_id 作用域查询、会话归属校验和合同报告访问控制的完整链路，并标明当前尚未实现的组织级多租户能力。</desc>',
    )
    add(
        lines,
        "<style>text{font-family:'Helvetica Neue',Helvetica,Arial,'PingFang SC','Microsoft YaHei','Microsoft JhengHei','SimHei',sans-serif;}</style>",
    )
    add(lines, "<defs>")
    for marker_id, color in (
        ("arrow-blue", "#2563eb"),
        ("arrow-purple", "#9333ea"),
        ("arrow-green", "#059669"),
        ("arrow-orange", "#ea580c"),
        ("arrow-gray", "#6b7280"),
    ):
        add(
            lines,
            f'<marker id="{marker_id}" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
            f'<polygon points="0 0, 10 3.5, 0 7" fill="{color}"/></marker>',
        )
    add(lines, "</defs>")
    add(lines, f'<rect data-graph-role="background" width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>')

    text(lines, 60, 48, "用户注册与租户隔离流程", size=28, weight=600)
    text(lines, 60, 80, "当前实现：user_id 作为隔离边界；每个注册账号相当于一个个人空间", size=14, fill="#6b7280")
    text(lines, 1310, 43, "图例", size=12, fill="#6b7280", weight=600)
    add(lines, '<line x1="1310" y1="64" x2="1340" y2="64" stroke="#2563eb" stroke-width="2" marker-end="url(#arrow-blue)"/>')
    text(lines, 1350, 68, "请求/响应", size=12, fill="#374151")
    add(lines, '<line x1="1460" y1="64" x2="1490" y2="64" stroke="#9333ea" stroke-width="2" marker-end="url(#arrow-purple)"/>')
    text(lines, 1500, 68, "身份变换", size=12, fill="#374151")
    add(lines, '<line x1="1610" y1="64" x2="1640" y2="64" stroke="#059669" stroke-width="2" marker-end="url(#arrow-green)"/>')
    text(lines, 1650, 68, "数据/允许", size=12, fill="#374151")

    # Swim lanes.
    add(lines, '<rect data-graph-role="container" x="45" y="105" width="1710" height="285" rx="14" fill="#f8fbff" stroke="#bfdbfe" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="45" y="425" width="1710" height="365" rx="14" fill="#faf8ff" stroke="#ddd6fe" stroke-width="1.4"/>')
    add(lines, '<rect data-graph-role="container" x="45" y="825" width="1710" height="300" rx="14" fill="#fffdf8" stroke="#fed7aa" stroke-width="1.4"/>')
    text(lines, 70, 135, "① 注册、登录与签发身份", size=16, fill="#1d4ed8", weight=600)
    text(lines, 70, 455, "② 每次请求的 user_id 租户边界", size=16, fill="#6b21a8", weight=600)
    text(lines, 70, 855, "③ 当前隔离能力与未实现边界", size=16, fill="#9a3412", weight=600)

    # Edges first, so cards remain readable.
    edge(lines, "e-auth-request", "M340 225 H420", source="browser-auth", target="auth-api", color="#2563eb", marker="arrow-blue", label="register / login", label_x=380, label_y=150)
    edge(lines, "e-api-service", "M720 225 H800", source="auth-api", target="auth-service", color="#2563eb", marker="arrow-blue", label="校验请求", label_x=760, label_y=202)
    edge(lines, "e-user-profile", "M1160 225 H1260", source="auth-service", target="user-profiles", color="#059669", marker="arrow-green", label="查重 + 写入", label_x=1210, label_y=202)
    edge(lines, "e-profile-response", "M1260 270 H1160", source="user-profiles", target="auth-service", color="#059669", marker="arrow-green", label="用户记录", label_x=1210, label_y=292)
    edge(lines, "e-jwt-response", "M980 280 V350 H340 V280", source="auth-service", target="browser-auth", color="#9333ea", marker="arrow-purple", label="JWT: sub=user_id, exp=24h", label_x=650, label_y=344)

    edge(lines, "e-request-guard", "M350 540 H470", source="protected-request", target="jwt-gate", color="#2563eb", marker="arrow-blue", label="Authorization: Bearer", label_x=410, label_y=475)
    edge(lines, "e-valid-yes", "M650 540 H790", source="jwt-gate", target="domain-api", color="#2563eb", marker="arrow-blue", label="有效 + 注入 user_id", label_x=720, label_y=517)
    edge(lines, "e-valid-no", "M560 580 V650", source="jwt-gate", target="unauthorized", color="#ea580c", marker="arrow-orange", label="无效 / 过期", label_x=620, label_y=620)
    edge(lines, "e-api-owner", "M1090 540 H1120", source="domain-api", target="owner-gate", color="#2563eb", marker="arrow-blue", label="review_id / session_id", label_x=1105, label_y=470)
    edge(lines, "e-owner-yes", "M1320 540 H1450", source="owner-gate", target="scoped-repository", color="#059669", marker="arrow-green", label="owner=user_id", label_x=1385, label_y=517)
    edge(lines, "e-owner-no", "M1220 580 V650", source="owner-gate", target="forbidden", color="#ea580c", marker="arrow-orange", label="不属于当前用户", label_x=1295, label_y=620)
    edge(lines, "e-user-space", "M1680 620 H1720 V1070 H460 V950", source="scoped-repository", target="personal-space", color="#059669", marker="arrow-green", label="按 user_id / session_id 过滤", label_x=1260, label_y=1050)
    edge(lines, "e-shared-rag", "M940 600 V780 H800 V895", source="domain-api", target="shared-legal-rag", color="#6b7280", marker="arrow-gray", label="共享法律 RAG", label_x=870, label_y=768, dashed=True)

    # Registration lane.
    card(lines, "browser-auth", 80, 170, 260, "浏览器 / 前端", "注册、登录、保存 token", fill="#eff6ff", stroke="#93c5fd", badge="WEB", badge_fill="#2563eb", status="入口", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "auth-api", 420, 170, 300, "Auth API", "/api/auth/register + /login", fill="#eff6ff", stroke="#93c5fd", badge="API", badge_fill="#2563eb", status="受保护", status_fill="#dbeafe", status_text="#1d4ed8")
    card(lines, "auth-service", 800, 170, 360, "AuthService + Security", "Argon2id 哈希 · JWT HS256", fill="#faf5ff", stroke="#d8b4fe", badge="AUTH", badge_fill="#9333ea", status="24 小时", status_fill="#ede9fe", status_text="#6b21a8")
    card(lines, "user-profiles", 1260, 170, 300, "user_profiles", "username 唯一 · user_id 主键", fill="#f0fdf4", stroke="#86efac", badge="DB", badge_fill="#059669", status="持久化", status_fill="#dcfce7", status_text="#166534")

    # Request lane.
    card(lines, "protected-request", 80, 490, 270, "受保护请求", "合同 / 对话 / 历史 / 报告", fill="#eff6ff", stroke="#93c5fd", badge="REQ", badge_fill="#2563eb", status="Bearer", status_fill="#dbeafe", status_text="#1d4ed8")
    decision(lines, "jwt-gate", 560, 540, 90, 40, "JWT 有效?", "decode_token")
    card(lines, "domain-api", 790, 490, 300, "API + Service", "user_id 进入业务调用上下文", fill="#faf5ff", stroke="#d8b4fe", badge="SVC", badge_fill="#9333ea", status="带身份", status_fill="#ede9fe", status_text="#6b21a8")
    decision(lines, "owner-gate", 1220, 540, 100, 40, "归属一致?", "owner = user_id")
    card(lines, "scoped-repository", 1450, 480, 280, "Repository 作用域", "WHERE user_id = JWT.sub", fill="#f0fdf4", stroke="#86efac", badge="SQL", badge_fill="#059669", status="隔离查询", status_fill="#dcfce7", status_text="#166534", height=140)
    card(lines, "unauthorized", 470, 650, 180, "401", "令牌无效或过期", fill="#fff7ed", stroke="#fdba74", badge="!", badge_fill="#ea580c", status="拒绝", status_fill="#fed7aa", status_text="#9a3412", height=70)
    card(lines, "forbidden", 1130, 650, 180, "403", "资源不属于当前用户", fill="#fff7ed", stroke="#fdba74", badge="!", badge_fill="#ea580c", status="拒绝", status_fill="#fed7aa", status_text="#9a3412", height=70)

    # Capability boundary lane.
    card(lines, "personal-space", 100, 895, 360, "个人隔离空间", "sessions · contract_reviews · reports", fill="#f0fdf4", stroke="#86efac", badge="U", badge_fill="#059669", status="已实现", status_fill="#dcfce7", status_text="#166534")
    card(lines, "shared-legal-rag", 620, 895, 360, "共享法律 RAG", "全国通用法条 / 官方案例语料", fill="#f3f4f6", stroke="#9ca3af", badge="RAG", badge_fill="#6b7280", status="共享", status_fill="#e5e7eb", status_text="#4b5563")
    note(lines, "future-tenant-model", 1140, 895, 500, "尚未实现：组织级多租户", [
        "tenant_id / organization / member roles",
        "跨用户共享、管理员权限、数据库 RLS",
    ])

    text(lines, 70, 1165, "注意：当前是“每个账号一个个人空间”的 user-level isolation，不等同于企业 SaaS 的 organization-level multi-tenancy。", size=12, fill="#6b7280")
    add(lines, "</svg>")
    return "\n".join(lines)


if __name__ == "__main__":
    OUTPUT.write_text(build_svg(), encoding="utf-8")
    print(f"generated: {OUTPUT}")
