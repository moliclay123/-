from datetime import datetime
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from utils.report_pdf_renderer import build_report_context, render_report_pdf


report_bp = Blueprint('report', __name__)


def _parse_datetime_value(value):
    if value is None:
        raise ValueError('缺少时间参数')

    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError('时间参数不能为空')

        normalized = text.replace('Z', '+00:00')
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            if 'T' in text and '.' in text:
                parsed = datetime.strptime(text.split('.')[0], '%Y-%m-%dT%H:%M:%S')
            elif 'T' in text:
                parsed = datetime.strptime(text, '%Y-%m-%dT%H:%M:%S')
            else:
                parsed = datetime.strptime(text, '%Y-%m-%d %H:%M:%S')

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)

    return parsed


def parse_report_range(payload):
    data = payload or {}
    start_time = _parse_datetime_value(data.get('startTime'))
    end_time = _parse_datetime_value(data.get('endTime'))

    if start_time > end_time:
        raise ValueError('开始时间不能晚于结束时间')

    return start_time, end_time


def parse_overview_layer(payload):
    data = payload or {}
    layer = str(data.get('overviewLayer') or 'core').strip().lower()
    if layer not in {'core', 'aggregation', 'access'}:
        layer = 'core'
    return layer


@report_bp.route('/preview', methods=['POST'])
def preview_report():
    try:
        payload = request.get_json(silent=True) or {}
        start_time, end_time = parse_report_range(payload)
        overview_layer = parse_overview_layer(payload)
        report_context = build_report_context(start_time, end_time, overview_layer)
        return jsonify({'code': 200, 'data': report_context['report']})
    except ValueError as error:
        return jsonify({'code': 400, 'msg': str(error)}), 400
    except Exception as error:
        return jsonify({'code': 500, 'msg': str(error)}), 500


@report_bp.route('/export_pdf', methods=['POST'])
def export_pdf():
    try:
        payload = request.get_json(silent=True) or {}
        start_time, end_time = parse_report_range(payload)
        overview_layer = parse_overview_layer(payload)
        report_context = build_report_context(start_time, end_time, overview_layer)
        pdf_bytes = render_report_pdf(report_context)
        return send_file(
            BytesIO(pdf_bytes),
            as_attachment=True,
            download_name=f"Report_{datetime.now().strftime('%Y%m%d%H%M')}.pdf",
            mimetype='application/pdf',
        )
    except ValueError as error:
        return jsonify({'code': 400, 'msg': str(error)}), 400
    except Exception as error:
        return jsonify({'code': 500, 'msg': f'PDF生成失败: {error}'}), 500
