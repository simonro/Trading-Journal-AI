import RuleBuilder from './RuleBuilder';
import SetupAssignments from './SetupAssignment';
import RuleVisibility from './RuleVisibility';

export default function Advanced({ history, onRulesSaved, onRuleVisibility, savingRule }) {
  return <div className="xl-advanced-body">
    <RuleBuilder onSaved={onRulesSaved} disabled={savingRule} setupOptions={history.setup_options || []} />
    <section className="xl-panel"><h2>Saved rules</h2>
      <RuleVisibility rules={history.custom_rules || []} onChange={onRuleVisibility} disabled={savingRule} />
      <SetupAssignments rules={history.assignment_rules || []} options={history.setup_options || []} onSaved={onRulesSaved} />
    </section>
  </div>;
}
