import base64
import io
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use('Agg')

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from flask import current_app, render_template

from utils.report_data_runtime import get_report_data

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False


CHART_COLORS = {
    'online': '#67C23A',
    'offline': '#F56C6C',
    'warning': '#E6A23C',
}

TOPOLOGY_CATEGORY_COLORS = {
    0: '#2e74ff',
    1: '#18b9db',
    2: '#2fc98f',
}

TOPOLOGY_STATUS_COLORS = {
    'online': '#2fc98f',
    'offline': '#ff6c7b',
    'unknown': '#8ca0b3',
}


def _create_default_topology():
    return {
        'hasSnapshot': False,
        'generatedAt': None,
        'nodes': [],
        'links': [],
        'summary': {
            'nodeCount': 0,
            'linkCount': 0,
            'onlineCount': 0,
            'offlineCount': 0,
        },
        'coreLinks': [],
    }


def _normalize_report_data(report_data):
    normalized = dict(report_data or {})
    normalized['genTime'] = normalized.get('genTime') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    normalized['summary'] = {
        'uptime': 0,
        'alertCount': 0,
        'busyDevice': '--',
        'conclusion': '暂无分析结论',
        **(normalized.get('summary') or {}),
    }
    normalized['overview'] = {
        'total': 0,
        'online': 0,
        'offline': 0,
        'selectedLayer': 'core',
        'selectedLayerLabel': '核心设备',
        'layerDevices': [],
        'devicesByLayer': {
            'core': [],
            'aggregation': [],
            'access': [],
        },
        'coreDevices': [],
        **(normalized.get('overview') or {}),
    }
    normalized['performance'] = {
        'topCpu': [],
        'topMem': [],
        'conclusion': '暂无性能分析数据',
        **(normalized.get('performance') or {}),
    }
    normalized['traffic'] = {
        'topInterfaces': [],
        'conclusion': '暂无接口流量分析数据',
        **(normalized.get('traffic') or {}),
    }
    normalized['alerts'] = {
        'total': 0,
        'distribution': {},
        'unresolved': [],
        **(normalized.get('alerts') or {}),
    }
    topology = _create_default_topology()
    topology.update(normalized.get('topology') or {})
    topology['summary'] = {
        **_create_default_topology()['summary'],
        **(topology.get('summary') or {}),
    }
    topology['nodes'] = list(topology.get('nodes') or [])
    topology['links'] = list(topology.get('links') or [])
    topology['coreLinks'] = list(topology.get('coreLinks') or [])
    normalized['topology'] = topology
    normalized['suggestions'] = list(normalized.get('suggestions') or ['暂无运维建议'])
    return normalized


def _buffer_to_data_uri(buffer, mime_type):
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:{mime_type};base64,{encoded}'


def _finalize_figure(fig, dpi=180):
    buffer = io.BytesIO()
    fig.savefig(buffer, format='png', bbox_inches='tight', facecolor='white', dpi=dpi)
    buffer.seek(0)
    plt.close(fig)
    return buffer


def _build_pie_chart_uri(report_data):
    online = float(report_data['overview'].get('online') or 0)
    offline = float(report_data['overview'].get('offline') or 0)
    if online + offline <= 0:
        return None

    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    ax.pie(
        [online, offline],
        labels=['在线设备', '离线设备'],
        autopct='%1.1f%%',
        startangle=140,
        colors=[CHART_COLORS['online'], CHART_COLORS['offline']],
        textprops={'fontsize': 10},
    )
    ax.set_title('设备状态分布', fontsize=12, pad=14)
    fig.tight_layout()
    return _buffer_to_data_uri(_finalize_figure(fig), 'image/png')


def _build_alert_bar_uri(report_data):
    distribution = report_data['alerts'].get('distribution') or {}
    if not distribution or sum(distribution.values()) <= 0:
        return None

    labels = list(distribution.keys())
    values = list(distribution.values())

    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    bars = ax.bar(labels, values, color=CHART_COLORS['warning'])
    ax.set_title('告警类型分布', fontsize=12, pad=14)
    ax.set_ylabel('数量')
    ax.set_axisbelow(True)
    ax.grid(axis='y', linestyle='--', alpha=0.25)

    for bar in bars:
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height,
            f'{int(height)}',
            ha='center',
            va='bottom',
            fontsize=9,
        )

    plt.xticks(rotation=15)
    fig.tight_layout()
    return _buffer_to_data_uri(_finalize_figure(fig), 'image/png')


def _normalize_topology_category(category):
    try:
        value = int(category)
    except (TypeError, ValueError):
        value = 2
    return value if value in (0, 1, 2) else 2


def _short_topology_label(name, limit=16):
    text = (name or '').strip()
    if len(text) <= limit:
        return text
    return f'{text[:limit - 3]}...'


def _build_topology_graph_uri(report_data):
    topology = report_data.get('topology') or {}
    nodes = list(topology.get('nodes') or [])
    links = list(topology.get('links') or [])
    if not nodes:
        return None

    grouped_nodes = {0: [], 1: [], 2: []}
    normalized_nodes = []
    for node in nodes:
        category = _normalize_topology_category(node.get('category'))
        normalized = {
            'id': node.get('id'),
            'name': node.get('name') or node.get('id') or '--',
            'status': node.get('status') or 'unknown',
            'category': category,
        }
        grouped_nodes[category].append(normalized)
        normalized_nodes.append(normalized)

    for category_nodes in grouped_nodes.values():
        category_nodes.sort(key=lambda item: item['name'])

    category_x = {
        0: 0.18,
        1: 0.50,
        2: 0.82,
    }
    category_titles = {
        0: '核心层',
        1: '汇聚层',
        2: '接入层',
    }
    positions = {}

    for category, category_nodes in grouped_nodes.items():
        total = len(category_nodes)
        for index, node in enumerate(category_nodes):
            if total <= 1:
                y = 0.5
            else:
                y = 0.84 - index * (0.64 / (total - 1))
            positions[node['id']] = (category_x[category], y)

    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('#ffffff')

    for category, label in category_titles.items():
        if grouped_nodes[category]:
            ax.text(
                category_x[category],
                0.94,
                label,
                ha='center',
                va='center',
                fontsize=10,
                fontweight='bold',
                color='#63788f',
            )

    for link in links:
        source = link.get('source')
        target = link.get('target')
        if source not in positions or target not in positions:
            continue
        x1, y1 = positions[source]
        x2, y2 = positions[target]
        ax.plot(
            [x1, x2],
            [y1, y2],
            color='#9ec5ff',
            linewidth=1.8,
            alpha=0.82,
            zorder=1,
        )

    for node in normalized_nodes:
        node_id = node['id']
        if node_id not in positions:
            continue
        x, y = positions[node_id]
        category = node['category']
        status = node['status']
        size = {0: 1600, 1: 1300, 2: 1000}.get(category, 1000)
        ax.scatter(
            x,
            y,
            s=size,
            color=TOPOLOGY_CATEGORY_COLORS.get(category, TOPOLOGY_CATEGORY_COLORS[2]),
            edgecolors=TOPOLOGY_STATUS_COLORS.get(status, TOPOLOGY_STATUS_COLORS['unknown']),
            linewidths=2.2,
            alpha=0.96,
            zorder=3,
        )
        ax.text(
            x,
            y - 0.075,
            _short_topology_label(node['name']),
            ha='center',
            va='top',
            fontsize=8.8,
            color='#1d3954',
            zorder=4,
        )

    ax.text(
        0.02,
        0.03,
        '节点边框颜色表示在线状态：绿色为在线，红色为离线',
        ha='left',
        va='bottom',
        fontsize=8,
        color='#7b8fa3',
    )
    ax.set_xlim(0.02, 0.98)
    ax.set_ylim(0.02, 0.98)
    ax.axis('off')
    fig.tight_layout(pad=0.8)
    return _buffer_to_data_uri(_finalize_figure(fig), 'image/png')


def _build_topology_graph_uri_v2(report_data):
    topology = report_data.get('topology') or {}
    nodes = list(topology.get('nodes') or [])
    links = list(topology.get('links') or [])
    if not nodes:
        return None

    grouped_nodes = {0: [], 1: [], 2: []}
    normalized_nodes = []
    for node in nodes:
        category = _normalize_topology_category(node.get('category'))
        normalized = {
            'id': node.get('id'),
            'name': node.get('name') or node.get('id') or '--',
            'status': node.get('status') or 'unknown',
            'category': category,
        }
        grouped_nodes[category].append(normalized)
        normalized_nodes.append(normalized)

    for category_nodes in grouped_nodes.values():
        category_nodes.sort(key=lambda item: item['name'])

    category_x = {0: 0.18, 1: 0.50, 2: 0.82}
    category_titles = {
        0: 'Core Layer',
        1: 'Aggregation Layer',
        2: 'Access Layer',
    }
    positions = {}

    for category, category_nodes in grouped_nodes.items():
        total = len(category_nodes)
        for index, node in enumerate(category_nodes):
            if total <= 1:
                y = 0.5
            else:
                y = 0.84 - index * (0.64 / (total - 1))
            positions[node['id']] = (category_x[category], y)

    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('#ffffff')

    lane_width = 0.24
    lane_height = 0.88
    lane_bottom = 0.06
    lane_styles = {
        0: ('#f2f7ff', '#d7e6ff'),
        1: ('#f0fbff', '#d2f4ff'),
        2: ('#f3fcf7', '#d9f6e6'),
    }

    for category, label in category_titles.items():
        if not grouped_nodes[category]:
            continue

        fill_color, edge_color = lane_styles[category]
        lane_left = category_x[category] - lane_width / 2
        ax.add_patch(
            FancyBboxPatch(
                (lane_left, lane_bottom),
                lane_width,
                lane_height,
                boxstyle='round,pad=0.012,rounding_size=0.03',
                linewidth=1.1,
                edgecolor=edge_color,
                facecolor=fill_color,
                zorder=0,
            )
        )
        ax.text(
            category_x[category],
            0.94,
            label,
            ha='center',
            va='center',
            fontsize=9.5,
            fontweight='bold',
            color='#63788f',
        )

    for link in links:
        source = link.get('source')
        target = link.get('target')
        if source not in positions or target not in positions:
            continue
        x1, y1 = positions[source]
        x2, y2 = positions[target]
        ax.plot(
            [x1, x2],
            [y1, y2],
            color='#9ec5ff',
            linewidth=2.0,
            alpha=0.84,
            zorder=1,
        )

    for node in normalized_nodes:
        node_id = node['id']
        if node_id not in positions:
            continue

        x, y = positions[node_id]
        category = node['category']
        status = node['status']
        size = {0: 1600, 1: 1300, 2: 1000}.get(category, 1000)
        ax.scatter(
            x,
            y,
            s=size,
            color=TOPOLOGY_CATEGORY_COLORS.get(category, TOPOLOGY_CATEGORY_COLORS[2]),
            edgecolors=TOPOLOGY_STATUS_COLORS.get(status, TOPOLOGY_STATUS_COLORS['unknown']),
            linewidths=2.2,
            alpha=0.96,
            zorder=3,
        )
        ax.text(
            x,
            y - 0.075,
            _short_topology_label(node['name']),
            ha='center',
            va='top',
            fontsize=8.8,
            color='#1d3954',
            zorder=4,
        )

    ax.text(
        0.03,
        0.94,
        'Border status',
        ha='left',
        va='center',
        fontsize=8.5,
        color='#7b8fa3',
        fontweight='bold',
    )
    for index, (label, color) in enumerate((
        ('Online', TOPOLOGY_STATUS_COLORS['online']),
        ('Offline', TOPOLOGY_STATUS_COLORS['offline']),
    )):
        y = 0.90 - index * 0.05
        ax.scatter(0.04, y, s=80, color='#ffffff', edgecolors=color, linewidths=2.0, zorder=5)
        ax.text(0.065, y, label, ha='left', va='center', fontsize=8, color='#7b8fa3')

    ax.set_xlim(0.02, 0.98)
    ax.set_ylim(0.02, 0.98)
    ax.axis('off')
    fig.tight_layout(pad=0.8)
    return _buffer_to_data_uri(_finalize_figure(fig), 'image/png')


def build_report_context(start_time, end_time, overview_layer='core'):
    report_data = _normalize_report_data(get_report_data(start_time, end_time, overview_layer))
    base_dir = Path(current_app.root_path)
    font_path = base_dir / 'simhei.ttf'

    return {
        'meta': {
            'title': '局域网运行分析报告',
            'system_name': '网络监控与自动运维平台',
            'start_time_text': start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time_text': end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'generated_at': report_data['genTime'],
            'font_face_url': font_path.as_uri() if font_path.exists() else None,
        },
        'report': report_data,
        'charts': {
            'overviewPie': _build_pie_chart_uri(report_data),
            'alertsBar': _build_alert_bar_uri(report_data),
            'topologyGraph': _build_topology_graph_uri_v2(report_data),
        },
        'base_url': str(base_dir),
    }


def render_report_html(report_context):
    return render_template('report_pdf.html', **report_context)


def _discover_browser_executable():
    configured = current_app.config.get('REPORT_BROWSER_EXECUTABLE')
    if configured and Path(configured).exists():
        return configured

    candidates = [
        shutil.which('msedge'),
        shutil.which('chrome'),
        shutil.which('chromium'),
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        '/usr/bin/google-chrome',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ]

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)

    raise RuntimeError('未找到可用的浏览器渲染器，请配置 REPORT_BROWSER_EXECUTABLE')


def _render_with_browser(html):
    browser = _discover_browser_executable()
    timeout_seconds = int(current_app.config.get('REPORT_BROWSER_TIMEOUT_SECONDS', 60))

    with tempfile.TemporaryDirectory(prefix='report_pdf_') as temp_dir:
        temp_path = Path(temp_dir)
        html_path = temp_path / 'report.html'
        pdf_path = temp_path / 'report.pdf'
        profile_path = temp_path / 'browser-profile'
        html_path.write_text(html, encoding='utf-8')
        profile_path.mkdir(exist_ok=True)

        command = [
            browser,
            '--headless=new',
            '--disable-gpu',
            '--allow-file-access-from-files',
            f'--user-data-dir={profile_path}',
            '--no-pdf-header-footer',
            f'--print-to-pdf={pdf_path}',
            html_path.as_uri(),
        ]

        if os.name == 'nt':
            escaped_browser = browser.replace("'", "''")
            escaped_args = ', '.join("'" + arg.replace("'", "''") + "'" for arg in command[1:])
            script = (
                f"$p = Start-Process -FilePath '{escaped_browser}' "
                f"-ArgumentList @({escaped_args}) -Wait -PassThru -WindowStyle Hidden; "
                "exit $p.ExitCode"
            )
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-Command', script],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        else:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )

        if result.returncode != 0 or not pdf_path.exists():
            raise RuntimeError(
                f'浏览器渲染 PDF 失败: {result.stderr.strip() or result.stdout.strip() or "unknown error"}'
            )

        return pdf_path.read_bytes()


def _render_with_html_renderer(html, base_url):
    try:
        from weasyprint import HTML
    except Exception as error:
        raise RuntimeError(f'HTML 渲染器不可用: {error}') from error

    return HTML(string=html, base_url=base_url).write_pdf()


def _render_with_reportlab_fallback(report_context):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_name = 'Helvetica'
    font_face_url = report_context['meta'].get('font_face_url')
    if font_face_url:
        font_path = font_face_url.replace('file:///', '')
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('SimHei', font_path))
            font_name = 'SimHei'

    styles = getSampleStyleSheet()
    normal = ParagraphStyle('NormalCN', parent=styles['Normal'], fontName=font_name, fontSize=10, leading=14)
    title = ParagraphStyle('TitleCN', parent=styles['Title'], fontName=font_name, fontSize=22, spaceAfter=16)
    heading = ParagraphStyle('HeadingCN', parent=styles['Heading1'], fontName=font_name, fontSize=15, spaceAfter=10)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=30, leftMargin=28, rightMargin=28, bottomMargin=30)
    elements = []
    report = report_context['report']
    meta = report_context['meta']

    elements.append(Paragraph(meta['title'], title))
    elements.append(Paragraph(f"分析时间范围：{meta['start_time_text']} 至 {meta['end_time_text']}", normal))
    elements.append(Paragraph(f"生成时间：{meta['generated_at']}", normal))
    elements.append(Spacer(1, 18))

    elements.append(Paragraph('分析摘要', heading))
    summary_rows = [
        ['网络在线率', f"{report['summary']['uptime']}%"],
        ['告警总数', str(report['summary']['alertCount'])],
        ['最繁忙设备', report['summary']['busyDevice']],
        ['核心结论', report['summary']['conclusion']],
    ]
    summary_table = Table(summary_rows, colWidths=[90, 390])
    summary_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f5f7fa')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dcdfe6')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 16))

    topology_graph = (report_context.get('charts') or {}).get('topologyGraph')
    if topology_graph:
        try:
            _, encoded_image = topology_graph.split(',', 1)
            topology_buffer = io.BytesIO(base64.b64decode(encoded_image))
            elements.append(Paragraph('缃戠粶鎷撴墤蹇収', heading))
            elements.append(Image(topology_buffer, width=520, height=240))
            elements.append(Spacer(1, 12))
        except Exception:
            pass

    sections = [
        (
            f"1. 网络概况 - {report['overview'].get('selectedLayerLabel', '核心设备')}",
            report['overview'].get('layerDevices') or report['overview'].get('coreDevices') or [],
            ['名称', 'IP', '状态'],
            ['name', 'ip', 'status']
        ),
        ('2. CPU Top 5', report['performance']['topCpu'], ['设备', '平均(%)', '峰值(%)'], ['name', 'avg', 'peak']),
        ('3. 内存 Top 5', report['performance']['topMem'], ['设备', '平均(%)', '峰值(%)'], ['name', 'avg', 'peak']),
        (
            '4. 接口流量',
            report['traffic']['topInterfaces'],
            ['设备', '接口', '平均(Mbps)', '峰值(Mbps)', '峰值时间'],
            ['device', 'interface', 'avgRate', 'peakRate', 'peakTime']
        ),
        ('5. 未恢复告警', report['alerts']['unresolved'], ['时间', '设备', '内容'], ['time', 'device', 'message']),
        ('6. 核心链路', report['topology']['coreLinks'], ['源设备', '目标设备', '链路标签'], ['sourceName', 'targetName', 'edgeLabel']),
    ]

    for title_text, rows, headers, keys in sections:
        elements.append(Paragraph(title_text, heading))
        table_rows = [headers]
        for row in rows or []:
            table_rows.append([str(row.get(key, '')) for key in keys])
        if len(table_rows) == 1:
            table_rows.append(['暂无数据'] + [''] * (len(headers) - 1))
        table = Table(table_rows, repeatRows=1)
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#409EFF')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dcdfe6')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 12))

    elements.append(Paragraph('7. 运维建议', heading))
    for suggestion in report['suggestions']:
        elements.append(Paragraph(f'• {suggestion}', normal))
        elements.append(Spacer(1, 6))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def render_report_pdf(report_context):
    renderer = (current_app.config.get('REPORT_PDF_RENDERER') or 'auto').lower()
    html = render_report_html(report_context)
    errors = []

    if renderer in {'auto', 'browser'}:
        try:
            return _render_with_browser(html)
        except Exception as error:
            errors.append(f'browser={error}')
            if renderer == 'browser':
                raise

    if renderer in {'auto', 'html'}:
        try:
            return _render_with_html_renderer(html, report_context['base_url'])
        except Exception as error:
            errors.append(f'html={error}')
            if renderer == 'html':
                raise

    current_app.logger.warning('Report PDF renderer fallback to reportlab: %s', '; '.join(errors))
    return _render_with_reportlab_fallback(report_context)
