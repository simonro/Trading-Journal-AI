import { PageHeader } from './ui';

const METRICS = [
  {
    category: 'Dashboard KPIs',
    items: [
      {
        name: 'Net P&L',
        formula: 'Sum of all closed trade P&L after commissions',
        description: 'Your total realized profit or loss for the selected period.',
        why: 'The bottom line. Every other metric is just an explanation for why this number is what it is.',
        target: null,
      },
      {
        name: 'Total Trades',
        formula: 'Count of completed round-trip positions',
        description: 'Number of trades that opened and closed within the period.',
        why: 'Provides denominator context. A 70% win rate on 10 trades is very different from 70% on 300 trades.',
        target: null,
      },
      {
        name: 'Trade Win %',
        formula: 'Winning trades / Total trades × 100',
        description: 'Percentage of individual trades that closed with a positive net P&L.',
        why: 'High win rate feels good but is meaningless without knowing your average win vs. loss size. A 40% win rate with a 3:1 reward:risk ratio beats a 70% win rate with 0.5:1.',
        target: '55-65% for momentum setups; 40%+ acceptable if avg win/loss > 2',
      },
      {
        name: 'Profit Factor',
        formula: 'Gross winning P&L / Gross losing P&L',
        description: 'Ratio of total money made on winners to total money lost on losers.',
        why: 'A profit factor above 1.0 means you make more than you lose. Below 1.0 is a losing system regardless of win rate. Values above 1.5 indicate a solid edge; above 2.0 is exceptional.',
        target: '1.5 or above',
      },
      {
        name: 'Day Win %',
        formula: 'Positive P&L days / Total trading days × 100',
        description: 'Percentage of trading sessions where you ended the day profitable.',
        why: 'Measures consistency at the session level, not just the trade level. A trader with 80% day win rate but a few large blowup days is a different risk profile than steady 60%.',
        target: '70-80%',
      },
      {
        name: 'Avg Win / Loss',
        formula: 'Average winning trade $ / Average losing trade $',
        description: 'How much you make on a typical winner relative to how much you lose on a typical loser.',
        why: 'Combined with win rate, this determines expectancy. If your ratio is below 1.0, you need a very high win rate just to break even. Most prop traders target 1.5-2.5.',
        target: '1.5 or above',
      },
      {
        name: 'Expectancy',
        formula: '(Win% × Avg Win) + (Loss% × Avg Loss)',
        description: 'Expected dollar value earned per trade, on average, over many trades.',
        why: 'The single most important metric for knowing if you have a real edge. Positive expectancy means the system makes money over time. Negative means it loses no matter how good your individual setups feel. $39/trade across 1,000 trades = $39,000.',
        target: 'Positive; $25+ per trade is strong for intraday',
      },
    ],
  },
  {
    category: 'Reports & Edge Analytics',
    items: [
      {
        name: 'Time-of-Day P&L',
        formula: 'Net P&L summed per 30-minute bucket (9:30 - 4:00)',
        description: 'Aggregate profit and loss broken down by the 30-minute window when the trade was entered.',
        why: 'Most intraday traders discover they lose money after a specific time. If your 1:00-2:30 PM bar is reliably red, that is a simple rule: stop trading after lunch. High-probability windows usually cluster in the first 90 minutes.',
        target: 'Identify your highest and lowest P&L windows, then focus or avoid accordingly',
      },
      {
        name: 'Day-of-Week P&L',
        formula: 'Net P&L summed per calendar day (Mon-Fri)',
        description: 'Aggregate profit and loss by day of the week across all traded sessions.',
        why: 'Many traders have consistent biases - bad Mondays (low conviction on thin pre-market), bad Fridays (profit-taking pressure), strong Thursdays. If the pattern persists over 100+ days, it is structural and tradeable.',
        target: 'Any day that is reliably negative over 30+ samples is worth sizing down or skipping',
      },
      {
        name: 'R-Multiple Distribution',
        formula: 'Actual P&L / Planned risk per trade, binned into 0.5R steps',
        description: 'A histogram of trade outcomes expressed as multiples of your planned risk. A 2R trade returned twice your stop loss distance; a -1R trade hit your stop.',
        why: 'This tells you whether your execution matches your plan. If you plan 2R targets but most trades are 0.3R scratches, you are cutting winners early. If there is a fat tail on the left side, you are not respecting stops. Requires R-multiple to be logged per trade.',
        target: 'Mode should be positive; distribution should be right-skewed (more positive outcomes than negative)',
      },
      {
        name: 'Emotion vs. Outcome',
        formula: 'Win rate and avg P&L grouped by logged emotional state',
        description: 'A breakdown of performance by the emotional state you logged: calm, anxious, overconfident, disciplined, frustrated, revenge.',
        why: '"Revenge" and "frustrated" rows almost always show the worst numbers. Seeing the actual dollar cost of trading angry is more powerful than any rule about not revenge trading. Requires emotional state to be logged per trade.',
        target: '"Calm" and "disciplined" rows should outperform your overall average; "revenge" should be a red flag',
      },
      {
        name: 'Hold Time (Winners vs. Losers)',
        formula: 'Avg minutes from first fill to last fill, split by outcome',
        description: 'Average time you hold winning trades vs. losing trades. Computed from execution timestamps.',
        why: 'The #1 behavioral bias of retail traders is holding losers too long and cutting winners too early. If your losers are held 2x longer than winners, you are hoping for recovery instead of respecting your plan.',
        target: 'Winners held longer than losers is a sign of good discipline',
      },
      {
        name: 'Mistake Frequency',
        formula: 'Count of each mistake tag from trade analysis and diary entries',
        description: 'A ranked list of the mistakes you make most often, sourced from your trade journal notes and AI tagging.',
        why: 'Repetition is the signal. You already know your rules. This chart shows which ones you break most. Fix one mistake at a time - the highest bar first.',
        target: 'Bars should shrink over time as you address each mistake systematically',
      },
    ],
  },
];

const FEATURES = [
  {
    name: 'Dashboard',
    icon: '🗂',
    description: 'Real-time overview of all KPIs, cumulative and daily P&L charts, a calendar heatmap, open positions, recent trades, and Time-of-Day and Day-of-Week charts.',
    tips: [
      'Use the date range picker to compare any period (e.g., this month vs. last month).',
      'Click any calendar day to jump directly to Day Review for that session.',
      'Use Edit goals in the Dashboard header to change your performance targets.',
    ],
  },
  {
    name: 'Goals',
    icon: '🎯',
    description: 'Editable performance targets for each KPI card. A progress bar appears below each metric showing how close you are to your goal, turning green when met.',
    tips: [
      'Open the Goals panel with Edit goals in the Dashboard header.',
      'Goals are saved per account - you can set different targets for your day trading vs. swing account.',
      'Default targets: Win Rate 65%, Profit Factor 1.5, Day Win Rate 75%, Expectancy $50, Avg Win/Loss 1.5.',
    ],
  },
  {
    name: 'Trade View',
    icon: '📋',
    description: 'Full trade log with filtering by date range, ticker, side, and strategy. Click any row to open execution detail and AI-generated coaching.',
    tips: [
      'Use the date filter to isolate a specific setup or time period.',
      'The AI coaching tab inside each trade pulls context from your diary and provides specific feedback.',
      'Trade charts open on the trade day 9:30-16:00 for 1m to 15m, the last month for 30m and 1H, and the last year for Daily and Weekly.',
      'Click Buy fill, Sell fill, VWAP, SL or Target above the chart to hide or show it.',
    ],
  },
  {
    name: 'Calendar',
    icon: '📅',
    description: 'Monthly heatmap with color-coded P&L per day. Week and month totals shown in the margins.',
    tips: [
      'Green days are profitable, red days are losses. Shade intensity reflects magnitude.',
      'Click any day to navigate directly to the Day Review for that session.',
    ],
  },
  {
    name: 'Day Review',
    icon: '📆',
    description: 'Deep dive into a single trading session. Shows all trades, individual P&L, AI daily coaching report, and the Weekly Summary generator.',
    tips: [
      'The AI coaching report grades your session based on execution quality, not just P&L.',
      'Use the Weekly Summary button to generate a cross-week behavioral synthesis (cached after first generation).',
      'The Circuit Breaker banner fires automatically after 3 consecutive losing trades in the session.',
    ],
  },
  {
    name: 'Edge',
    icon: '🎯',
    description: 'Four behavioral analytics charts: R-Multiple Distribution, Emotion vs. Outcome, Mistake Frequency, and Hold Time. These reveal patterns invisible in raw P&L.',
    tips: [
      'R-Multiple and Emotion data require filling in the analysis fields on each trade.',
      'Mistake frequency comes from both diary AI tagging and manual trade notes.',
      'Hold Time is computed automatically from execution timestamps - no manual logging needed.',
    ],
  },
  {
    name: 'Import',
    icon: '📤',
    description: 'CSV importer for Thinkorswim account statements, Interactive Brokers (IBKR) Activity Statements and Robinhood activity reports. Pick the broker from the dropdown (or leave Auto-detect). Automatically parses executions, groups them into trades, and detects duplicates on re-import. See the README for a Claude Code prompt that adds any other broker.',
    tips: [
      'Re-importing a file is safe - duplicates are detected by trade group and skipped.',
      'If you deleted a bad trade and need to re-import it, the re-import will restore it cleanly.',
    ],
  },
  {
    name: 'Strategies',
    icon: '📊',
    description: 'P&L, win rate, and trade count broken down by strategy name. Strategy names come from your diary AI analysis.',
    tips: [
      'Upload diary entries to start populating strategy data.',
      'Strategies with negative P&L across 10+ trades are signals to reduce size or stop trading that setup.',
    ],
  },
  {
    name: 'Diary',
    icon: '📓',
    description: 'AI-powered trade diary. Upload handwritten notes, screenshots, or typed text. Claude extracts setup, entry/exit reason, emotional state, mistakes, and R-multiple.',
    tips: [
      'The more detail you write in your pre/post-market notes, the better the AI extraction.',
      'Diary entries are matched to trades automatically by ticker and date.',
    ],
  },
  {
    name: 'Settings',
    icon: '⚙️',
    description: 'Manage the names the journal uses: strategies with descriptions, sources, and tags by type. Add, rename, merge duplicates or delete. Reports has a matching Sources & Tags tab.',
    tips: [
      'Merging A into B moves every trade from A to B. If a later diary analysis says A, it is saved as B.',
      'Deleting a name that trades use asks whether to reassign them to another name or leave them blank.',
    ],
  },
  {
    name: 'Brain',
    icon: '🧠',
    description: 'AI chatbot with full context of your trade history. Ask any question about your performance and patterns.',
    tips: [
      'Ask: "What is my best performing setup?" or "When do I tend to revenge trade?"',
      'Brain has access to all your trades, P&L, and analysis - it answers from your actual data, not generic advice.',
      'Open Brain from the Brain button in the header or the floating Brain button at the bottom right.',
    ],
  },
  {
    name: 'Weekly Summary',
    icon: '📝',
    description: 'AI-generated behavioral synthesis for the week. Available as a collapsible card in Day Review. Identifies the week\'s anchor mistake, standout edge, and one rule to carry forward.',
    tips: [
      'Generated on demand, cached after the first generation.',
      'Best run at the end of the trading week (Friday) to capture the full picture.',
      'Use "Force regenerate" to get a fresh analysis if you logged more trades or diary entries.',
    ],
  },
  {
    name: 'Circuit Breaker',
    icon: '🔶',
    description: 'An orange banner that appears automatically in Day Review when you have 3 or more consecutive losing trades in the current session.',
    tips: [
      'This is not a hard stop - it is a pause prompt. The research on this is clear: a brief pause after 3 losses significantly reduces revenge trading.',
      'Dismiss it once you have consciously reviewed and decided to continue.',
    ],
  },
];

function MetricCard({ item }) {
  return (
    <div className="help-card">
      <h4 style={{ fontWeight: 600, fontSize: 16, marginBottom: 8 }}>{item.name}</h4>
      <div className="help-formula">{item.formula}</div>
      <div style={{ fontSize: 14, color: 'var(--text-primary)', marginBottom: 10, lineHeight: 1.55 }}>{item.description}</div>
      <div style={{ fontSize: 13.5, color: 'var(--text-secondary)', lineHeight: 1.55, marginBottom: item.target ? 10 : 0 }}>
        <span style={{ color: 'var(--accent-line)', fontWeight: 600 }}>Why it matters: </span>{item.why}
      </div>
      {item.target && (
        <div style={{ fontSize: 13.5, color: 'var(--text-secondary)', lineHeight: 1.55 }}>
          <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>Target: </span>{item.target}
        </div>
      )}
    </div>
  );
}

function FeatureCard({ feature }) {
  return (
    <div className="help-card">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <span className="help-icon" aria-hidden="true">{feature.icon}</span>
        <h4 style={{ fontWeight: 600, fontSize: 16 }}>{feature.name}</h4>
      </div>
      <div style={{ fontSize: 14, color: 'var(--text-primary)', lineHeight: 1.55, marginBottom: 10 }}>{feature.description}</div>
      {feature.tips.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13.5, color: 'var(--text-secondary)', lineHeight: 1.7 }}>
          {feature.tips.map((tip, i) => <li key={i}>{tip}</li>)}
        </ul>
      )}
    </div>
  );
}

export default function Help() {
  return (
    <div>
      <PageHeader
        title="Help and Reference"
        subtitle="What each metric means and where every feature lives."
      />

      {METRICS.map(section => (
        <div key={section.category} style={{ marginBottom: 'var(--space-6)' }}>
          <h2 className="section-title" style={{ marginBottom: 12 }}>{section.category}</h2>
          <div className="help-grid">
            {section.items.map(item => <MetricCard key={item.name} item={item} />)}
          </div>
        </div>
      ))}

      <div style={{ marginBottom: 'var(--space-6)' }}>
        <h2 className="section-title" style={{ marginBottom: 12 }}>Features</h2>
        <div className="help-grid">
          {FEATURES.map(f => <FeatureCard key={f.name} feature={f} />)}
        </div>
      </div>
    </div>
  );
}
