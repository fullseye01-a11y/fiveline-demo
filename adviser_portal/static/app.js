"use strict";
const fmt = (n) => "NT$" + Math.round(Number(n)).toLocaleString("zh-TW");
const pct = (n) => Number(n).toFixed(1) + "%";
const $ = (q) => document.querySelector(q);
const sample = { positions: [], rr: 3 };
const order = ["tw_equity", "global_equity", "bonds", "cash"];
const labels = {tw_equity:"台灣股票",global_equity:"全球股票",bonds:"債券",cash:"現金"};
function showToast(message) {
  const toast=$("#toast"); toast.textContent=message;toast.classList.add("show");
  clearTimeout(window.__toastTimeout);window.__toastTimeout=setTimeout(()=>toast.classList.remove("show"),5200);
}
function switchPanel(id) {
  document.querySelectorAll(".panel").forEach(p=>p.classList.toggle("active",p.id===id));
  document.querySelectorAll(".nav-btn").forEach(b=>{
    b.classList.toggle("selected",b.dataset.panel===id);
    b.setAttribute("aria-current",b.dataset.panel===id?"page":"false");
  });
  window.scrollTo({top:0,behavior:"smooth"});
}
document.addEventListener("click",(e)=>{
  const btn=e.target.closest("[data-goto],[data-panel]");
  if(btn){ const id=btn.dataset.goto||btn.dataset.panel;switchPanel(id); }
});
async function post(url,payload){
  const res=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},
                              body:JSON.stringify(payload),cache:"no-store",credentials:"omit"});
  const value=await res.json();
  if(!res.ok) throw new Error(value.error||"試算無法完成");
  return value;
}
function fromForm(form){return Object.fromEntries(new FormData(form).entries());}
function clearChildren(node){node.replaceChildren();}
function el(tag,klass,value){const out=document.createElement(tag);if(klass)out.className=klass;if(value!==undefined)out.textContent=String(value);return out;}
function line(container,label,value,negative=false){
  const row=el("div","trade-item");row.append(el("span","",label));
  const val=el("strong",negative?"negative":"",value);row.append(val);container.append(row);
}
async function assess(ev){
  if(ev)ev.preventDefault();
  const p=fromForm($("#risk-form"));Object.keys(p).forEach(k=>p[k]=Number(p[k]));
  const d=await post("/api/risk",p);sample.rr=d.rr;
  $("#risk-tier").textContent="RR"+d.rr;
  $("#summary-rr").textContent="RR"+d.rr;
  $("#risk-capacity").textContent=d.capacity_score+" / 100";
  $("#risk-willingness").textContent=d.willingness_score+" / 100";
  $("#capacity-bar").style.width=d.capacity_score+"%";
  $("#willingness-bar").style.width=d.willingness_score+"%";
  $("#risk-explain").textContent=d.method+"。此結果為非正式模型，尚未完成商品適合度評估。";
  $("#allocation-rr").value=String(d.rr);
}
function loadPositions(rows){
  const container=$("#position-fields");clearChildren(container);
  rows.forEach((p,i)=>{
    const div=el("div","position-line"),wrap=el("div","");
    wrap.append(el("strong","",p.name),el("small","",labels[p.asset]+" · 科技占比假設 "+p.tech_pct+"%"));
    const inp=el("input","");
    inp.type="number";inp.min="0";inp.max="1000000000";inp.step="1000";
    inp.value=String(p.value);inp.dataset.index=String(i);inp.setAttribute("aria-label",p.name+"金額");
    div.append(wrap,inp);container.append(div);
  });
}
async function allocate(ev){
  if(ev)ev.preventDefault();
  const pos=sample.positions.map((p,i)=>{
    const input=document.querySelector('input[data-index="'+i+'"]');
    return {...p,value:Number(input.value)};
  });
  const payload={rr:Number($("#allocation-rr").value),
                 new_money:Number($("#allocation-form").elements.new_money.value),
                 positions:pos};
  const d=await post("/api/allocation",payload);
  $("#summary-assets").textContent=fmt(d.total_existing);
  const bars=$("#allocation-breakdown");clearChildren(bars);
  order.forEach(k=>{
    const row=el("div","bar-row");
    const title=el("div","bar-title");title.append(el("span","",labels[k]),
      el("strong","",pct(d.current_weights_pct[k])+" → "+pct(d.target_weights_pct[k])));
    const rail=el("div","bar-rail"),fill=el("div","bar-fill");
    fill.style.width=Math.min(100,d.current_weights_pct[k])+"%";
    fill.setAttribute("role","img");fill.setAttribute("aria-label",labels[k]+"既有配置百分比 "+d.current_weights_pct[k]);
    rail.append(fill);row.append(title,rail);bars.append(row);
  });
  $("#exposure-tw").textContent=pct(d.tw_equity_pct_of_existing);
  $("#exposure-global").textContent=pct(d.global_equity_pct_of_existing);
  $("#exposure-tech").textContent=pct(d.tech_overlap_pct_of_existing);
  const grad=$("#gradual-result"),reb=$("#rebalance-result");clearChildren(grad);clearChildren(reb);
  order.forEach(k=>{
    const g=d.gradual_buy_only[k],v=d.immediate_net_trades[k];
    line(grad,labels[k],(g>0?"+":"")+fmt(g));
    line(reb,labels[k],(v>0?"+":"")+fmt(v),v<0);
  });
  line(grad,"新增資金總額",fmt(d.new_money));
  line(reb,"淨流入／投入",fmt(d.new_money));
}
async function retire(ev){
  if(ev)ev.preventDefault();
  const payload=fromForm($("#retirement-form"));
  for(const [k,v]of Object.entries(payload)){payload[k]=Number(v);}
  const d=await post("/api/retirement",payload);
  const root=$("#retirement-results");clearChildren(root);
  const max=Math.max(...d.scenarios.map(x=>x.ending_nominal),1);
  d.scenarios.forEach(sc=>{
    const box=el("div","scenario"),head=el("div","scenario-head");
    head.append(el("b","",sc.scenario),el("small","",sc.gross_return_pct+"% 年報酬假設"));
    box.append(head,el("strong","",fmt(sc.ending_nominal)),
      el("div","scenario-sub","按目前購買力："+fmt(sc.ending_today_value)
           +(sc.target_achievement_pct===null?"":" · 目標 "+pct(sc.target_achievement_pct))));
    if(sc.exhausted_year!==null)box.append(el("div","scenario-sub","資金可能於第 "+sc.exhausted_year+" 年耗盡"));
    const rail=el("div","scenario-progress"),fill=el("span","");
    fill.style.width=(sc.ending_nominal/max*100)+"%";
    rail.append(fill);box.append(rail);root.append(box);
  });
}
function submitSafely(fn){return async function(e){try{await fn(e);}catch(err){showToast(err.message||"發生未知錯誤");}}}
document.addEventListener("DOMContentLoaded",async()=>{
  $("#risk-form").addEventListener("submit",submitSafely(assess));
  $("#allocation-form").addEventListener("submit",submitSafely(allocate));
  $("#retirement-form").addEventListener("submit",submitSafely(retire));
  try{
    const req=await fetch("/api/demo",{cache:"no-store"});
    if(!req.ok)throw new Error("示範資料讀取失敗");
    const d=await req.json();
    if(d.synthetic!==true || d.login_available!==false)throw new Error("展示站安全狀態不符");
    sample.positions=d.positions;loadPositions(sample.positions);
    await assess();await allocate();await retire();
  }catch(err){showToast("初始化尚未完成："+err.message);}
});
