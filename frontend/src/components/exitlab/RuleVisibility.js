export default function RuleVisibility({ rules, onChange, disabled }) {
  if (!rules.length) return null;
  return <div className="xl-your-rules" role="group" aria-label="Your rules">
    <strong>Your rules</strong>
    {rules.map(rule => <label key={rule.id} className={`xl-chip xl-rule-visibility ${rule.show_in_tables ? 'on' : ''}`}>
      <span>{rule.name}</span><span className="xl-rule-switch"><input type="checkbox" role="switch" aria-label={`Show in tables: ${rule.name}`} checked={Boolean(rule.show_in_tables)} disabled={disabled} onChange={e => onChange(rule.id, e.target.checked)} />Show in tables</span>
    </label>)}
  </div>;
}
