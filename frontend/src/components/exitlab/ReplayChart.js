import { useEffect, useRef } from 'react';
import { createChart, LineStyle } from 'lightweight-charts';
import { clockTime, number } from './shared';
const tradePrice = price => `$${number(price)}`;
export function customExitMarkers(replay) {
  return (replay.rules || []).filter(r => r.is_preset === false).flatMap(r => {
    const outcome = r.trades[0];
    if (!outcome?.eligible) return [];
    return (outcome.fills || []).map(f => ({ ...f, label: r.rule.name }));
  });
}

export default function ReplayChart({ replay }) {
  const container = useRef(null);
  useEffect(() => {
    const root = container.current;
    if (!root || !replay.bars.length) return;
    const css = getComputedStyle(document.documentElement);
    const token = name => css.getPropertyValue(name).trim();
    const ink = token('--text-secondary'), amber = token('--accent-line');
    const green = token('--result-pos'), red = token('--result-neg');
    const time = ts => Math.floor(new Date(ts).getTime() / 1000);
    const trade = replay.trade;
    const chartBars = replay.bars.filter(b => time(b.ts) >= time(trade.entered_at) - 15 * 60);
    const chart = createChart(root, {
      width: root.clientWidth, height: 260,
      layout: { background: { color: token('--surface-inset') }, textColor: ink, fontFamily: css.getPropertyValue('--font-mono').trim(), fontSize: 11 },
      grid: { vertLines: { color: token('--divider-soft') }, horzLines: { color: token('--divider-soft') } },
      rightPriceScale: { borderColor: token('--divider'), entireTextOnly: true, scaleMargins: { top: 0.2, bottom: 0.2 } },
      timeScale: { borderColor: token('--divider'), timeVisible: true, secondsVisible: false, tickMarkFormatter: t => clockTime(t * 1000) },
      localization: { timeFormatter: t => `${clockTime(t * 1000)} New York` },
    });
    const levels = trade.stop == null ? [trade.entry] : [trade.entry, trade.stop, ...[1, 2].map(r => trade.entry + trade.direction * Math.abs(trade.entry - trade.stop) * r)];
    const candles = chart.addCandlestickSeries({ upColor: green, downColor: red, wickUpColor: green, wickDownColor: red, borderVisible: false, priceLineVisible: false, lastValueVisible: false,
      autoscaleInfoProvider: base => { const info = base(); return info ? { ...info, priceRange: { minValue: Math.min(info.priceRange.minValue, ...levels), maxValue: Math.max(info.priceRange.maxValue, ...levels) } } : null; } });
    candles.setData(chartBars.map(b => ({ time: time(b.ts), open: b.open, high: b.high, low: b.low, close: b.close })));
    const line = (price, title, color) => candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title });
    line(trade.entry, 'Entry', ink);
    if (trade.stop != null) {
      line(trade.stop, 'Stop', red);
      [1, 2].forEach(r => line(trade.entry + trade.direction * Math.abs(trade.entry - trade.stop) * r, `${r}R`, r === 1 ? amber : green));
    }
    const last = time(chartBars[chartBars.length - 1].ts);
    const marker = (fills, label, color, shape, labelPosition) => {
      // One point series per fill preserves multiple tranche prices in the same minute.
      fills.forEach(f => {
        const series = chart.addLineSeries({ color, lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 4, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: true });
        const ts = Math.min(Math.floor(time(f.ts) / 60) * 60, last);
        series.setData([{ time: ts, value: f.price }]);
        series.setMarkers([{ time: ts, position: labelPosition || 'inBar', color, shape, text: labelPosition ? label : '', size: 1 }]);
      });
    };
    const opening = replay.scaled_entry?.fills || [{ ts: trade.entered_at, price: trade.entry }];
    opening.forEach((f, i) => marker([f], `Entry ${i + 1}`, ink, 'circle', replay.scaled_entry ? 'belowBar' : undefined));
    marker([{ ts: trade.exited_at, price: trade.exit }], 'Actual', token('--text-primary'), 'square');
    (replay.first_hits || []).forEach(hit => marker([hit], hit.label, hit.label === 'Stop' ? red : hit.label === '1R' ? amber : green, 'circle'));
    customExitMarkers(replay).forEach((f, i) => marker([f], f.label, amber, trade.direction === 1 ? 'arrowDown' : 'arrowUp', i % 2 ? 'belowBar' : 'aboveBar'));
    const fit = () => chart.timeScale().setVisibleLogicalRange({ from: -4, to: chartBars.length + 3 });
    fit();
    const observer = new ResizeObserver(() => { chart.applyOptions({ width: root.clientWidth }); fit(); });
    observer.observe(root);
    return () => { observer.disconnect(); chart.remove(); };
  }, [replay]);
  return <section className="xl-panel xl-chart"><div className="xl-panel-head"><h2>Trade replay</h2><span className="xl-muted">1-minute bars / New York time</span></div>
    <div ref={container} className="xl-candles" aria-label="Historical minute candles with opening price, stop, R levels and selected exit markers" role="img" />
    <div className="xl-chart-key"><span>Opening fill: {tradePrice(replay.trade.entry)}</span>{replay.trade.stop != null && <><span>Initial stop: {tradePrice(replay.trade.stop)}</span>{[1, 2].map(r => <span key={r}>+{r}R: {tradePrice(replay.trade.entry + replay.trade.direction * Math.abs(replay.trade.entry - replay.trade.stop) * r)}</span>)}</>}</div>
    {replay.scaled_entry && <div className="xl-chart-key"><span>Entry line: quantity-weighted average</span>{replay.scaled_entry.fills.map((f, i) => <span key={i}>Entry {i + 1}: {f.quantity} shares / {clockTime(f.ts)} / {tradePrice(f.price)}</span>)}</div>}
    <div className="xl-chart-key"><span>Square: actual exit {clockTime(replay.trade.exited_at)}</span>{(replay.first_hits || []).map(hit => <span key={hit.label} style={{ color: hit.label === 'Stop' ? 'var(--result-neg)' : hit.label === '1R' ? 'var(--accent-line)' : 'var(--result-pos)' }}>Circle: {hit.label} touch {clockTime(hit.ts)}</span>)}</div>
    {customExitMarkers(replay).length > 0 && <div className="xl-chart-key xl-custom-markers">{customExitMarkers(replay).map((f, i) => <span key={i}>Arrow: {f.label} / {clockTime(f.ts)} / {tradePrice(f.price)}</span>)}</div>}
  </section>;
}
