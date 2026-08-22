const scoreCsrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));

async function scoreApi(method, path, body) {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: {'Content-Type':'application/json', 'X-CSRF-Token':scoreCsrf},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function showScoreTab(name) {
  document.querySelectorAll('[data-score-tab]').forEach((button) => button.classList.toggle('active', button.dataset.scoreTab === name));
  document.querySelectorAll('[data-score-panel]').forEach((panel) => panel.classList.toggle('active', panel.dataset.scorePanel === name));
  location.hash = `scorecard-${name}`;
}

document.querySelectorAll('[data-score-tab]').forEach((button) => button.addEventListener('click', () => showScoreTab(button.dataset.scoreTab)));
const initialTab = location.hash.replace('#scorecard-', '');
if (document.querySelector(`[data-score-tab="${CSS.escape(initialTab)}"]`)) showScoreTab(initialTab);
else if (!document.querySelector('[data-score-tab].active')) document.querySelector('[data-score-tab]')?.click();

document.querySelectorAll('[data-open-dialog]').forEach((button) => button.addEventListener('click', () => document.getElementById(button.dataset.openDialog)?.showModal()));
document.querySelectorAll('[data-close-dialog]').forEach((button) => button.addEventListener('click', () => button.closest('dialog')?.close()));

const agentForm = document.getElementById('agentForm');
document.querySelectorAll('[data-edit-agent]').forEach((button) => button.addEventListener('click', () => {
  const agent = JSON.parse(button.dataset.editAgent);
  Object.entries(agent).forEach(([key, value]) => {
    const input = agentForm?.elements.namedItem(key);
    if (!input) return;
    if (input.type === 'checkbox') input.checked = Boolean(value); else input.value = value ?? '';
  });
  document.getElementById('agentDialog').showModal();
}));
agentForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(agentForm));
  data.active = agentForm.elements.active.checked;
  const id = data.id; delete data.id;
  try { await scoreApi(id ? 'PUT' : 'POST', id ? `/api/scorecard/agents/${id}` : '/api/scorecard/agents', data); location.hash='#scorecard-agents'; location.reload(); }
  catch (error) { alert(error.message); }
});
document.querySelectorAll('[data-toggle-agent]').forEach((button) => button.addEventListener('click', async () => {
  try { await scoreApi('POST', `/api/scorecard/agents/${button.dataset.toggleAgent}/toggle`, {}); location.hash='#scorecard-agents'; location.reload(); }
  catch (error) { alert(error.message); }
}));

document.getElementById('newScorecardForm')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const record = await scoreApi('POST', '/api/scorecard/scorecards', Object.fromEntries(new FormData(event.currentTarget)));
    event.currentTarget.closest('dialog').close(); await openScorecard(record.id);
  } catch (error) { alert(error.message); }
});

let currentScorecard = null;
function calcPoints(row, achieved, target) {
  if (achieved === '' || achieved === null) return 0;
  const value = Number(achieved), divisor = target === '' || target === null || Number(target) === 0 ? null : Number(target);
  const ratio = divisor ? value / divisor : value;
  for (const rule of row.scoring_rules || []) {
    const threshold = rule.threshold; let yes = false;
    if (rule.operator === '>=') yes = ratio >= Number(threshold);
    if (rule.operator === '>') yes = ratio > Number(threshold);
    if (rule.operator === '<=') yes = ratio <= Number(threshold);
    if (rule.operator === '<') yes = ratio < Number(threshold);
    if (rule.operator === '==') yes = ratio === Number(threshold);
    if (rule.operator === 'between' && Array.isArray(threshold)) yes = ratio >= Number(threshold[0]) && ratio <= Number(threshold[1]);
    if (yes) return Math.min(Number(rule.points || 0), Number(row.max_points || 0));
  }
  if (!(row.scoring_rules || []).length) return Math.max(0, Math.min(divisor ? ratio : (value > 1 ? value/100 : value), 1)) * Number(row.max_points || 0);
  return 0;
}
function updateScorePreview() {
  if (!currentScorecard) return;
  const zero = document.querySelector('#scorecardForm [name="zero_score_triggered"]')?.checked;
  let points = 0, max = 0;
  document.querySelectorAll('#scorecardEditorRows tr').forEach((tr) => {
    const row = currentScorecard.rows.find((item) => item.id === Number(tr.dataset.rowId));
    const result = zero ? 0 : calcPoints(row, tr.querySelector('[data-field="achieved"]').value, tr.querySelector('[data-field="target"]').value);
    tr.querySelector('[data-preview]').textContent = `${result.toFixed(2)} / ${Number(row.max_points).toFixed(2)}`;
    points += result; max += Number(row.max_points || 0);
  });
  document.getElementById('scorecardTotal').textContent = `${max ? (points/max*100).toFixed(1) : '0'}%`;
}
async function openScorecard(id) {
  try {
    currentScorecard = await scoreApi('GET', `/api/scorecard/scorecards/${id}`);
    const form = document.getElementById('scorecardForm');
    form.elements.id.value = id; form.elements.evaluation_date.value = currentScorecard.evaluation_date;
    form.elements.positive.value = currentScorecard.positive_feedback || '';
    form.elements.development.value = currentScorecard.area_of_development || '';
    form.elements.zero_score_triggered.checked = Boolean(currentScorecard.zero_score_triggered);
    document.getElementById('scorecardTitle').textContent = `${currentScorecard.employee_name} — ${currentScorecard.department}`;
    document.getElementById('scorecardSubtitle').textContent = `${currentScorecard.emp_id} · ${currentScorecard.status}`;
    document.getElementById('scorecardExcel').href = `/scorecard/scorecards/${id}/excel`;
    const conditions = document.getElementById('zeroConditions');
    conditions.textContent = currentScorecard.zero_score_conditions.length ? `Zero-score conditions: ${currentScorecard.zero_score_conditions.join(' · ')}` : '';
    conditions.hidden = !currentScorecard.zero_score_conditions.length;
    document.getElementById('scorecardEditorRows').innerHTML = currentScorecard.rows.map((row) => `<tr data-row-id="${row.id}"><td><strong>${esc(row.section)}</strong><small>${esc(row.kpi)}</small></td><td>${esc(row.grid_scoring || '—')}</td><td><input type="number" step="any" data-field="achieved" value="${row.achieved ?? ''}"></td><td><input type="number" step="any" data-field="target" value="${row.target ?? ''}"></td><td><b data-preview>${row.final_points} / ${row.max_points}</b></td><td><input data-field="remarks" value="${esc(row.manager_remarks || '')}"></td></tr>`).join('');
    document.querySelectorAll('#scorecardEditorRows input, #scorecardForm [name="zero_score_triggered"]').forEach((input) => input.addEventListener('input', updateScorePreview));
    updateScorePreview(); document.getElementById('scorecardDialog').showModal();
  } catch (error) { alert(error.message); }
}
document.querySelectorAll('[data-open-scorecard]').forEach((button) => button.addEventListener('click', () => openScorecard(button.dataset.openScorecard)));

async function saveScorecard(status) {
  const form = document.getElementById('scorecardForm');
  const rows = [...document.querySelectorAll('#scorecardEditorRows tr')].map((tr) => ({id:Number(tr.dataset.rowId), achieved:tr.querySelector('[data-field="achieved"]').value, target:tr.querySelector('[data-field="target"]').value, manager_remarks:tr.querySelector('[data-field="remarks"]').value}));
  await scoreApi('PUT', `/api/scorecard/scorecards/${form.elements.id.value}`, {evaluation_date:form.elements.evaluation_date.value, positive:form.elements.positive.value, development:form.elements.development.value, zero_score_triggered:form.elements.zero_score_triggered.checked, status, rows});
  location.hash='#scorecard-scorecards'; location.reload();
}
document.querySelectorAll('[data-save-status]').forEach((button) => button.addEventListener('click', async () => { try { await saveScorecard(button.dataset.saveStatus); } catch(error) { alert(error.message); } }));

function parseGrid(text) {
  const rules = [];
  String(text || '').split(/\n|;/).forEach((line) => {
    const pointMatch = line.match(/(?:=|:|—|-)\s*([\d.]+)\s*(?:pts?|points?)?\s*$/i);
    if (!pointMatch) return;
    const condition = line.slice(0, pointMatch.index).trim(); const points = Number(pointMatch[1]); let match;
    const factor = condition.includes('%') ? .01 : 1;
    if ((match = condition.match(/([\d.]+)%?\s*[-–]\s*([\d.]+)%?/))) rules.push({operator:'between',threshold:[Number(match[1])*factor,Number(match[2])*factor],points});
    else if ((match = condition.match(/([\d.]+)%?\s+and\s+(?:above|over)/i))) rules.push({operator:'>=',threshold:Number(match[1])*factor,points});
    else if ((match = condition.match(/(?:≥|>=)\s*([\d.]+)%?/))) rules.push({operator:'>=',threshold:Number(match[1])*factor,points});
    else if ((match = condition.match(/(?:≤|<=)\s*([\d.]+)%?/))) rules.push({operator:'<=',threshold:Number(match[1])*factor,points});
    else if ((match = condition.match(/(?:less\s+than|<)\s*([\d.]+)%?/i))) rules.push({operator:'<',threshold:Number(match[1])*factor,points});
    else if ((match = condition.match(/^([\d.]+)%?/))) rules.push({operator:'==',threshold:Number(match[1])*factor,points});
  });
  return rules;
}
function templateRow(metric={}) {
  return `<tr><td><input data-field="section" value="${esc(metric.section || 'BUSINESS PARAMETERS/KPI')}"></td><td><input data-field="kpi" value="${esc(metric.kpi || '')}"></td><td><textarea data-field="grid_scoring">${esc(metric.grid_scoring || '')}</textarea></td><td><input data-field="kra" value="${esc(metric.kra || '')}"></td><td><input type="number" data-field="weightage" value="${metric.weightage ?? 0}"></td><td><input type="number" data-field="max_points" value="${metric.max_points ?? 0}"></td><td><button type="button" class="text-button" data-remove-row>×</button></td></tr>`;
}
function bindRemoveRows() { document.querySelectorAll('[data-remove-row]').forEach((button) => button.onclick = () => button.closest('tr').remove()); }
document.getElementById('newTemplateButton')?.addEventListener('click', () => {
  document.getElementById('templateForm').reset(); document.getElementById('templateRows').innerHTML = templateRow(); bindRemoveRows(); document.getElementById('templateDialog').showModal();
});
document.getElementById('addTemplateRow')?.addEventListener('click', () => { document.getElementById('templateRows').insertAdjacentHTML('beforeend', templateRow()); bindRemoveRows(); });
document.querySelectorAll('[data-edit-template]').forEach((button) => button.addEventListener('click', async () => {
  try { const template = await scoreApi('GET', `/api/scorecard/templates/${encodeURIComponent(button.dataset.editTemplate)}`); const form=document.getElementById('templateForm'); form.elements.department.value=template.department; form.elements.name.value=template.name; form.elements.zero_score_conditions.value=(template.zero_score_conditions||[]).join('\n'); document.getElementById('templateRows').innerHTML=template.metrics.map(templateRow).join(''); bindRemoveRows(); document.getElementById('templateDialog').showModal(); } catch(error) { alert(error.message); }
}));
document.getElementById('templateForm')?.addEventListener('submit', async (event) => {
  event.preventDefault(); const form=event.currentTarget;
  const metrics=[...document.querySelectorAll('#templateRows tr')].map((tr) => { const get=(name)=>tr.querySelector(`[data-field="${name}"]`).value; const grid=get('grid_scoring'); return {section:get('section'),kpi:get('kpi'),grid_scoring:grid,kra:get('kra'),weightage:Number(get('weightage')),max_points:Number(get('max_points')),scoring_rules:parseGrid(grid)}; });
  try { await scoreApi('POST','/api/scorecard/templates',{department:form.elements.department.value,name:form.elements.name.value,zero_score_conditions:form.elements.zero_score_conditions.value.split('\n').map(v=>v.trim()).filter(Boolean),metrics}); location.hash='#scorecard-templates'; location.reload(); } catch(error) { alert(error.message); }
});

document.getElementById('newAppraisalForm')?.addEventListener('submit', async (event) => {
  event.preventDefault(); try { const record=await scoreApi('POST','/api/scorecard/appraisals',Object.fromEntries(new FormData(event.currentTarget))); event.currentTarget.closest('dialog').close(); await openAppraisal(record.id); } catch(error) { alert(error.message); }
});
let currentAppraisal=null;
function updateAppraisalPreview() {
  if (!currentAppraisal) return; let weighted=0, weights=0;
  document.querySelectorAll('#appraisalEditorRows tr').forEach((tr) => { const rating=Number(tr.querySelector('[data-field="rating"]').value); const row=currentAppraisal.rows.find(item=>item.id===Number(tr.dataset.rowId)); if(rating){weighted+=rating*row.weightage;weights+=row.weightage;} });
  const behaviour=weights?weighted/weights:null; const kpi=Number(document.querySelector('#appraisalForm [name="kpi_rating_5"]').value);
  document.getElementById('appraisalTotal').textContent = behaviour && kpi ? `${((kpi*.6+behaviour*.4)*2).toFixed(2)} /10` : '— /10';
}
async function openAppraisal(id) {
  try { currentAppraisal=await scoreApi('GET',`/api/scorecard/appraisals/${id}`); const form=document.getElementById('appraisalForm');
    ['id','period_label','review_date','kpi_rating_5','overall_band','increment_pct','strengths','improvements'].forEach((name)=>{if(form.elements[name]) form.elements[name].value=currentAppraisal[name]??'';});
    form.elements.learning.value=currentAppraisal.learning_requirements||''; form.elements.promotion.value=currentAppraisal.promotion_recommendation||'';
    document.getElementById('appraisalTitle').textContent=`${currentAppraisal.employee_name} — Appraisal`; document.getElementById('appraisalSubtitle').textContent=`${currentAppraisal.period_label} · KPI suggestion: ${currentAppraisal.kpi_suggestion ?? 'no finalized scorecards'}`; document.getElementById('appraisalExcel').href=`/scorecard/appraisals/${id}/excel`;
    document.getElementById('appraisalEditorRows').innerHTML=currentAppraisal.rows.map((row)=>`<tr data-row-id="${row.id}"><td>${esc(row.section)}</td><td><strong>${esc(row.criteria)}</strong></td><td>${esc(row.guidance)}</td><td>${row.weightage}</td><td><select data-field="rating"><option value="">—</option>${[1,2,3,4,5].map(v=>`<option ${row.rating===v?'selected':''}>${v}</option>`).join('')}</select></td><td><input data-field="comments" value="${esc(row.comments||'')}"></td></tr>`).join('');
    document.querySelectorAll('#appraisalEditorRows select, #appraisalForm [name="kpi_rating_5"]').forEach(el=>el.addEventListener('input',updateAppraisalPreview)); updateAppraisalPreview(); document.getElementById('appraisalDialog').showModal();
  } catch(error){alert(error.message);}
}
document.querySelectorAll('[data-open-appraisal]').forEach(button=>button.addEventListener('click',()=>openAppraisal(button.dataset.openAppraisal)));
async function saveAppraisal(status){const form=document.getElementById('appraisalForm');const rows=[...document.querySelectorAll('#appraisalEditorRows tr')].map(tr=>({id:Number(tr.dataset.rowId),rating:tr.querySelector('[data-field="rating"]').value,comments:tr.querySelector('[data-field="comments"]').value}));const body=Object.fromEntries(new FormData(form));body.status=status;body.rows=rows;await scoreApi('PUT',`/api/scorecard/appraisals/${form.elements.id.value}`,body);location.hash='#scorecard-appraisals';location.reload();}
document.querySelectorAll('[data-appraisal-status]').forEach(button=>button.addEventListener('click',async()=>{try{await saveAppraisal(button.dataset.appraisalStatus);}catch(error){alert(error.message);}}));

async function sendResultEmail(type, formId) {
  const form=document.getElementById(formId); const recipient=prompt('Confirm the recipient work email:', type==='scorecard'?currentScorecard.employee_email:currentAppraisal.employee_email); if(!recipient)return;
  if(!confirm(`Send this finalized ${type} to ${recipient}?`))return;
  try {const result=await scoreApi('POST',`/api/scorecard/${type}/${form.elements.id.value}/email`,{recipient});alert(result.message);location.reload();}catch(error){alert(error.message);}
}
document.getElementById('sendScorecardEmail')?.addEventListener('click',()=>sendResultEmail('scorecard','scorecardForm'));
document.getElementById('sendAppraisalEmail')?.addEventListener('click',()=>sendResultEmail('appraisal','appraisalForm'));

document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('click',(event)=>{if(event.target===dialog)dialog.close();}));
document.querySelector('#newScorecardForm [name="evaluation_date"]')?.setAttribute('value',new Date().toISOString().slice(0,10));
document.querySelector('#newAppraisalForm [name="review_date"]')?.setAttribute('value',new Date().toISOString().slice(0,10));
