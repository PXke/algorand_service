import 'package:algorand_platform/core/ui/article_chart.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('parseArticleChartSpec accepts line chart JSON', () {
    const raw = '''
{
  "type": "line",
  "title": "ALGO price (USD)",
  "x": ["07-01", "07-02", "07-03"],
  "series": [{"name": "ALGO", "y": [0.15, 0.16, 0.155]}]
}
''';
    final spec = parseArticleChartSpec(raw);
    expect(spec, isNotNull);
    expect(spec!.type, 'line');
    expect(spec.title, 'ALGO price (USD)');
    expect(spec.labels, ['07-01', '07-02', '07-03']);
    expect(spec.series.single.values, [0.15, 0.16, 0.155]);
  });

  test('parseArticleChartSpec accepts bar chart with two series', () {
    const raw = '''
{"type":"bar","title":"Fees","x":["Legacy","Algorand"],
 "series":[{"name":"USD","y":[4.5,0.001]}]}
''';
    final spec = parseArticleChartSpec(raw);
    expect(spec?.type, 'bar');
    expect(spec?.series.single.name, 'USD');
  });

  test('parseArticleChartSpec rejects mismatched lengths', () {
    const raw =
        '{"type":"line","title":"x","x":["a","b"],"series":[{"name":"s","y":[1]}]}';
    expect(parseArticleChartSpec(raw), isNull);
  });

  test('parseArticleChartSpec rejects invalid JSON', () {
    expect(parseArticleChartSpec('not json'), isNull);
  });
}
