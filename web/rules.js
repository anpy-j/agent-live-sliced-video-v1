/* Shared business rules. S2 pinyin is the first supported rule family. */
window.renderRules = async function () {
  setCrumb('规则管理');
  const [snapshot, jobData] = await Promise.all([api('/api/rules/pinyin'), api('/api/rules/pinyin/jobs')]);
  if (location.hash !== '#/rules') return;
  app.innerHTML = `<div class="page-head"><div><h1>规则管理</h1><p>S2 拼音规则 · 所有剪辑版本共用</p></div></div>
    <section class="panel rules-panel"><h2>连续拼音限制</h2>
    <p>按无声调音节连续匹配。保存后在下一次 S2 执行时生效。</p>
    <form id="pinyinForm" class="rules-form">
      <label class="field">拼音音节<input name="pinyin" required placeholder="例如：xian huo" autocomplete="off"></label>
      <label class="field">备注<input name="note" maxlength="1000" placeholder="可选，例如：现货话术"></label>
      <div><button class="button primary" type="submit">保存</button> <button class="button ghost" type="button" id="pinyinCancel" hidden>取消编辑</button></div>
    </form>
    <p id="pinyinMessage" role="status"></p>
    <div id="pinyinList"></div></section>
    <section class="panel rules-panel"><h2>从已完成任务试匹配</h2>
    <p>选择任务和要测试的拼音规则，读取全部 S2 子句进行匹配。</p>
    <div class="rules-test-controls"><label class="field">已完成任务<select id="pinyinJob" aria-label="已完成任务"><option value="">选择任务</option>${jobData.jobs.map((j, i) => `<option value="${i}">${escapeHtml(j.title)} · ${escapeHtml(j.version)}</option>`).join('')}</select></label>
    <label class="field">规则拼音<select id="pinyinTestRule" aria-label="规则拼音"><option value="">选择规则拼音</option></select></label>
    <button type="button" class="button primary" id="pinyinTest">开始测试</button></div>
    ${jobData.jobs.length ? '' : '<p>暂无带 S2 数据的已完成任务。</p>'}
    <div id="pinyinResults" aria-live="polite"></div></section>`;
  let rules = snapshot.rules, editing = null, result = null;
  const form = $('#pinyinForm');
  const message = (text) => { $('#pinyinMessage').textContent = text; };
  const reset = () => { editing = null; form.reset(); $('button[type="submit"]', form).textContent = '保存'; $('#pinyinCancel').hidden = true; };
  const clearResult = () => { result = null; $('#pinyinResults').innerHTML = '<p>规则已变化，请重新测试。</p>'; };
  const list = () => {
    const selected = $('#pinyinTestRule').value;
    $('#pinyinTestRule').innerHTML = '<option value="">选择规则拼音</option>' + rules.map(r => `<option value="${r.id}">${escapeHtml(r.pinyin)}${r.note ? ' · ' + escapeHtml(r.note) : ''}${r.enabled ? '' : '（已停用，可试匹配）'}</option>`).join('');
    $('#pinyinTestRule').value = rules.some(r => r.id === selected) ? selected : '';
    $('#pinyinList').innerHTML = `<p>共 ${rules.length} 条</p>${rules.length ? `<ul class="pinyin-rule-list">${rules.map(r => `<li><span class="pinyin-rule-value">${escapeHtml(r.pinyin)}${r.enabled ? '' : ' <small>已停用</small>'}${r.note ? '<br><small>' + escapeHtml(r.note) + '</small>' : ''}</span><div><button class="button ghost small" data-edit-rule="${r.id}">编辑</button> <button class="button ghost small" data-toggle-rule="${r.id}">${r.enabled ? '停用' : '启用'}</button> <button class="button ghost small" data-delete-rule="${r.id}">删除</button></div></li>`).join('')}</ul>` : '<p>暂无拼音规则。</p>'}`;
  };
  $('#pinyinCancel').onclick = reset;
  form.onsubmit = async event => {
    event.preventDefault();
    const button = $('button[type="submit"]', form); button.disabled = true;
    try {
      const fields = new FormData(form);
      const saved = await api(`/api/rules/pinyin${editing ? '/' + editing : ''}`, {method: editing ? 'PUT' : 'POST', body: JSON.stringify({pinyin: fields.get('pinyin'), note: fields.get('note'), enabled: editing ? rules.find(r => r.id === editing).enabled : true})});
      rules = saved.rules; reset(); list(); clearResult(); message('规则已保存。');
    } catch (error) { message(error.message); } finally { button.disabled = false; }
  };
  $('#pinyinList').onclick = async event => {
    const button = event.target.closest('button'); if (!button) return;
    const id = button.dataset.editRule || button.dataset.toggleRule || button.dataset.deleteRule;
    const rule = rules.find(r => r.id === id); if (!rule) return;
    if (button.dataset.editRule) {
      editing = id;
      form.elements.pinyin.value = rule.pinyin;
      form.elements.note.value = rule.note || '';
      $('button[type="submit"]', form).textContent = '保存修改'; $('#pinyinCancel').hidden = false;
      form.elements.pinyin.focus(); return;
    }
    button.disabled = true;
    try {
      const saved = await api('/api/rules/pinyin/' + id, button.dataset.deleteRule ? {method: 'DELETE'} : {method: 'PUT', body: JSON.stringify({...rule, enabled: !rule.enabled})});
      rules = saved.rules; if (editing === id) reset(); list(); clearResult(); message('规则已更新。');
    } catch (error) { message(error.message); button.disabled = false; }
  };
  const showResults = (hitsOnly = true) => {
    const rows = result.rows.filter(r => !hitsOnly || r.hits.length);
    $('#pinyinResults').innerHTML = `<p>共 ${result.total} 条子句，命中 ${result.matched} 条；其中原 S2 通过、现在会剔除 ${result.newly_rejected} 条。</p>
      <label class="rules-check"><input id="pinyinHitsOnly" type="checkbox" ${hitsOnly ? 'checked' : ''}> 只看命中句</label>
      <div class="rules-table-wrap"><table class="rules-table"><thead><tr><th>时间 / ID</th><th>原文 / 拼音</th><th>原 S2 结果</th><th>当前规则命中</th></tr></thead><tbody>${rows.map(r => `<tr><td>${Number(r.start || 0).toFixed(2)}–${Number(r.end || 0).toFixed(2)}s<br>${escapeHtml(r.id)}</td><td>${escapeHtml(r.text)}<br><small>${escapeHtml(r.pinyin)}</small></td><td>${r.s2_usable ? '通过' : '已剔除'}<br><small>${escapeHtml(reasonLabels[r.s2_reason] || r.s2_reason)}</small></td><td>${r.hits.map(h => `${escapeHtml(h.pinyin)}：${escapeHtml(h.text)}`).join('<br>') || '未命中'}</td></tr>`).join('')}</tbody></table></div>${rows.length ? '' : '<p>没有命中句。</p>'}`;
    $('#pinyinHitsOnly').onchange = event => showResults(event.target.checked);
  };
  $('#pinyinTest').onclick = async () => {
    const value = $('#pinyinJob').value;
    if (value === '') { $('#pinyinResults').textContent = '请先选择已完成任务。'; return; }
    const ruleId = $('#pinyinTestRule').value;
    if (!ruleId) { $('#pinyinResults').textContent = '请选择要匹配的规则拼音。'; return; }
    const job = jobData.jobs[Number(value)], button = $('#pinyinTest'); button.disabled = true;
    $('#pinyinResults').textContent = '正在匹配任务 S2 数据…';
    try { result = await api('/api/rules/pinyin/test', {method: 'POST', body: JSON.stringify({job_id: job.id, kind: job.kind, rule_id: ruleId})}); showResults(); }
    catch (error) { $('#pinyinResults').textContent = error.message; } finally { button.disabled = false; }
  };
  $('#pinyinJob').onchange = () => { result = null; $('#pinyinResults').innerHTML = ''; };
  $('#pinyinTestRule').onchange = () => { result = null; $('#pinyinResults').innerHTML = ''; };
  list();
};
