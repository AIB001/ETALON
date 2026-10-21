"""Render a self-contained interactive view of recorded CPU evidence (no new runs)."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('workspace', type=Path)
args = parser.parse_args()
root = args.workspace.resolve()
rounds = [r for r in json.loads((root / 'round-trace.json').read_text()) if r['outcome']['actions']]
payload = json.dumps(rounds, ensure_ascii=False).replace('<', '\\u003c')
html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ETALON CPU 实测追踪</title>
<style>
body{font:16px/1.7 system-ui,sans-serif;background:#f4f6f9;color:#182438;margin:0}
main{max-width:1100px;margin:32px auto;padding:0 24px}h1{font-size:30px;line-height:1.3}
.card{background:white;padding:24px;border-radius:14px;margin:18px 0;border:1px solid #dee5ee}
.muted{color:#52647a;font-size:14px}.pill{background:#e6f1ee;color:#145840;padding:6px 12px;border-radius:24px;display:inline-block}
button{border:1px solid #bac9d9;background:white;padding:8px 16px;border-radius:8px;cursor:pointer;margin:3px}
button[aria-pressed=true]{background:#183f70;color:white}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:9px;text-align:left;border-bottom:1px solid #e4eaf1}
svg{width:100%;height:auto}code{font-size:14px}.orange{color:#a84d18}
@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:0 12px}.card{padding:16px}}
</style><main>
<div class="pill">真实 CPU 计算 · 8 个分子 · 16/16 观测准入 · 8 轮</div>
<h1>一条观测如何进入下一轮选择</h1>
<p>ETALON 5927aceb + 已记录的 Windows 文件 I/O 兼容补丁。目标是最小化分子量；第 1 轮后加入 logP 端点。这里展示软件执行证据，不代表药效验证。</p>
<div class="card"><strong>选择轮次</strong><div id="rounds"></div><p id="status" class="muted"></p>
<table><thead><tr><th>本轮候选</th><th>计算端点</th><th>选择原因</th><th>选择分数</th><th>实际读数</th><th>准入</th></tr></thead><tbody id="choices"></tbody></table></div>
<div class="card"><strong>本轮前后：目标分子量的模型预测</strong>
<p class="muted">蓝色＝计算前，橙色＝加入本轮观测后。误差棒是 ±1 个模型后验标准差，不是校准后的置信区间。第 5 轮同时测量苯和乙醇。</p>
<div id="chart"></div></div>
<div class="card"><strong>仅在模型中屏蔽乙醇标签，会发生什么？</strong>
<p class="muted">离线诊断，保留真实动作与花费，把乙醇观测标为未准入，再重算模型与选择；不改变原始账本，不是另一场实际实验。</p>
<div id="counter"></div></div>
<div class="grid"><div class="card"><strong>乙醇通过的真实路径</strong>
<p><code>ethanol, CCO</code> → CSV 快照 → 分子身份标准化 → RDKit 属性 → <b>46.069 Da</b> → 身份、产物哈希、协议、最终存活与标量检查 → 写入 SQLite → 下一轮 GP 更新。</p>
<p>原始示例没有药物性质阈值。其 <code>checks=[]</code>；没有运行 MD 物理质量检查。</p></div>
<div class="card"><strong>单独运行的拒绝对照</strong>
<p>同样先算出 46.069 Da，再加显式 <code>MW ≥ 50 Da</code> 筛选：<span class="orange">PROPERTY_MW_BELOW_MIN → CANDIDATE_FILTERED</span>。</p>
<p>原始读数保留，训练集大小为 0，花费仍记 1 个示例额度。拒绝表达“不满足这条协议”，不表示分子量算错。</p></div></div>
<p class="muted">边界：1033 维输入含 MolWt、MolLogP，所以分子量/logP 已在特征中。示例不能验证预测未知标签的能力。24 个成本额度是配置值，不是 CPU 小时或实际费用。数据来源：本目录 JSON 与 SQLite 账本；补丁见 local-windows-compat.patch。</p>
</main><script>
const data=__DATA__;
const names={'ethanol':'乙醇','ethylamine':'乙胺','acetamide':'乙酰胺','acetic-acid':'乙酸','benzene':'苯','phenol':'苯酚','propanol':'丙醇','pyridine':'吡啶'};
const reasons={'objective-bootstrap':'目标端点启动采样','paired-calibration':'跨端点配对校准','cost-aware-information':'信息收益 / 成本','uncertainty-exploration':'不确定性探索'};
const fmt=x=>Number(x).toFixed(3), el=id=>document.getElementById(id);
function chart(r){
 const ids=Object.keys(r.before.predictions['mw-v1']);
 const x=v=>145+Math.max(-10,Math.min(120,v))*6;
 let s='<svg viewBox="0 0 900 450" role="img" aria-label="分子量模型预测前后对比">';
 for(let t=0;t<=120;t+=20)s+=`<line x1="${x(t)}" x2="${x(t)}" y1="20" y2="415" stroke="#e7edf4"/><text x="${x(t)}" y="440" font-size="12" fill="#52647a">${t} Da</text>`;
 ids.forEach((id,i)=>{let y=40+i*49;s+=`<text x="0" y="${y+5}" font-size="14">${names[id]}</text>`;
  [['before','#2467b0',-7],['after','#c96b2c',7]].forEach(([key,color,d])=>{let p=r[key].predictions['mw-v1'][id];s+=`<line x1="${x(p.mean-p.sd)}" x2="${x(p.mean+p.sd)}" y1="${y+d}" y2="${y+d}" stroke="${color}" stroke-width="2"/><circle cx="${x(p.mean)}" cy="${y+d}" r="4" fill="${color}"><title>${names[id]} ${key}: ${fmt(p.mean)} ± ${fmt(p.sd)} Da</title></circle>`;});
 }); return s+'</svg>';
}
function show(i){
 const r=data[i];document.querySelectorAll('button').forEach((b,k)=>b.setAttribute('aria-pressed',String(k===i)));
 el('status').textContent=`第 ${i+1} 轮 · 训练观测 ${r.before.model.training_size} → ${r.after.model.training_size} · 累计花费 ${r.after.balance.spent}/24 个示例额度`;
 el('choices').innerHTML=r.before.choices.map(c=>{let o=r.observations.find(o=>o.result.candidate_id===c.candidate&&o.result.endpoint_id===c.endpoint);return `<tr><td>${names[c.candidate]} (${c.candidate})</td><td>${c.endpoint}</td><td>${reasons[c.evidence.reason]}</td><td>${c.score>1e7?c.score.toExponential(2):fmt(c.score)}</td><td>${fmt(o.result.value)} ${o.result.units}</td><td>${o.admitted?'通过':'拒绝'}</td></tr>`}).join('');
 el('chart').innerHTML=chart(r);
 let cf=r.offline_without_ethanol_labels;
 if(!cf){el('counter').textContent='截至本轮尚未获得乙醇观测，暂不进行此诊断。';return;}
 el('counter').innerHTML='<table><thead><tr><th>分子</th><th>真实账本模型：均值 ± 标准差</th><th>屏蔽乙醇标签后</th></tr></thead><tbody>'+['ethanol','ethylamine','acetic-acid'].map(id=>{let a=r.after.predictions['mw-v1'][id],b=cf.predictions['mw-v1'][id];return `<tr><td>${names[id]}</td><td>${fmt(a.mean)} ± ${fmt(a.sd)} Da</td><td>${fmt(b.mean)} ± ${fmt(b.sd)} Da</td></tr>`}).join('')+'</tbody></table>'+`<p>实际下一批：${r.after.choices.map(c=>names[c.candidate]+' / '+c.endpoint).join('，')||'停止'}<br>诊断下一批：${cf.choices.map(c=>names[c.candidate]+' / '+c.endpoint).join('，')||'停止'}</p><p class="muted">第 5 轮后两种情形选择相同分子，预测与分数不同；不能据此声称乙醇观测改变了下一批成员。</p>`;
}
el('rounds').innerHTML=data.map((_,i)=>`<button onclick="show(${i})" aria-pressed="false">${i+1}</button>`).join('');show(4);
</script></html>'''
(root / 'walkthrough.html').write_text(html.replace('__DATA__', payload), encoding='utf-8')
print(root / 'walkthrough.html')
