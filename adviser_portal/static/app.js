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
  if(id==="catalog") ensureCatalog();
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


// Catalog is a public identity directory only. This page never saves selected
// fund/share-class identifiers or uses them to manufacture portfolio returns.
const catalog = {initialized:false, busy:false, query:"", kind:"all", offset:0,
                 matched:0, selected:new Map()};
const KIND_LABELS = {
  domestic_fund_share_class:"境內基金",
  offshore_fund_share_class:"在台核備境外基金",
  etf:"上市／上櫃 ETF",
  listed_stock:"上市股票",
  otc_stock:"上櫃股票"
};
function renderCatalogSelection(){
  const holder=$("#catalog-selected");
  holder.replaceChildren();
  if(!catalog.selected.size){holder.textContent="尚未加入標的。";return;}
  for(const row of catalog.selected.values()){
    const card=el("div","selected-line");
    const description=el("div","");
    description.append(el("strong","",row.name),el("small","",
      row.code+"　"+(row.currency||"幣別未標示")+"　"+(row.isin||"ISIN 尚無資料")));
    const remove=el("button","ghost","移除");
    remove.type="button";remove.dataset.removeUid=row.uid;
    remove.setAttribute("aria-label","移除 "+row.name);
    card.append(description,remove);holder.append(card);
  }
}
function renderCatalogItem(row){
  const card=el("article","catalog-record");
  const title=el("strong","",row.name);
  const kind=el("span","catalog-kind",KIND_LABELS[row.category]||"市場標的");
  const info=el("div","catalog-identity","代碼 "+row.code+" · "+(row.currency||"幣別待核對")+
       (row.isin?" · ISIN "+row.isin:" · ISIN 未提供"));
  const note=el("p","catalog-note","身分及級別尚待核准；非即時淨值與回測績效");
  const btn=el("button","ghost",catalog.selected.has(row.uid)?"已加入候選":"加入候選觀察");
  btn.type="button";btn.dataset.addUid=row.uid;
  btn.setAttribute("aria-label","將 "+row.name+" 加入本次候選觀察名單");
  btn.disabled=catalog.selected.has(row.uid);
  btn.addEventListener("click",()=>{
    if(catalog.selected.size>=20){showToast("單次最多暫存 20 個候選標的");return;}
    catalog.selected.set(row.uid,row);
    btn.textContent="已加入候選";btn.disabled=true;
    renderCatalogSelection();
  });
  card.append(kind,title,info,note);
  if(row.category==="domestic_fund_share_class" && row.source==="sitca_domestic_funds"){
    const evidenceButton=el("button","ghost catalog-nav-btn","查看官方每單位 NAV");
    evidenceButton.type="button";
    const evidenceArea=el("div","catalog-nav-evidence");
    evidenceArea.setAttribute("aria-live","polite");
    evidenceButton.addEventListener("click",async()=>{
      evidenceButton.disabled=true;
      evidenceButton.textContent="核對官方來源中…";
      evidenceArea.replaceChildren(el("span","","首次查詢可能需等待免費後端喚醒。"));
      const controller=new AbortController();
      const timeout=setTimeout(()=>controller.abort(),35000);
      try{
        const url=new URL("/api/nav-evidence",location.origin);
        url.searchParams.set("uid",row.uid);
        const response=await fetch(url.toString(),{
          cache:"no-store",credentials:"omit",signal:controller.signal
        });
        const value=await response.json();
        if(!response.ok)throw Error(value.error||"官方資料暫時無法核對");
        if(value.instrument_uid!==row.uid || value.usable_for_portfolio!==false
           || value.class_identity_approved!==false)throw Error("基金級別安全驗證未通過");
        evidenceArea.replaceChildren();
        if(value.status==="LATEST_NAV_EVIDENCE_ONLY" && value.nav!==null){
          const nav=Number(value.nav);
          if(!Number.isFinite(nav)||nav<=0)throw Error("NAV 格式異常");
          const shown=nav.toLocaleString("zh-TW",{
            minimumFractionDigits:3,maximumFractionDigits:3
          });
          evidenceArea.append(
            el("strong","catalog-nav-value",shown+" "+value.currency+"／單位"),
            el("small","","淨值有效日 "+value.nav_date+" · 來源：SITCA 公開資料")
          );
          evidenceArea.append(el("p","catalog-nav-warning",value.stale_warning
            ?"來源已超過七天，請確認資料日期；此配對僅供人工核對，不可直接試算。"
            :"名稱、代碼及幣別已一致，但基金級別與含息報酬仍未正式核准。"));
        }else{
          evidenceArea.append(el("p","catalog-nav-warning",
            value.reason||"尚未找到可唯一核對的單位 NAV，不能自動配對。"));
        }
      }catch(error){
        evidenceArea.replaceChildren(el("p","catalog-nav-warning",
          error.name==="AbortError"?"官方 NAV 查詢逾時，請稍後重試。":error.message));
      }finally{
        clearTimeout(timeout);
        evidenceButton.disabled=false;
        evidenceButton.textContent="重新核對官方 NAV";
      }
    });
    card.append(evidenceButton,evidenceArea);
  }else if(row.category==="offshore_fund_share_class"){
    card.append(el("p","catalog-nav-warning",
      "境外基金每單位 NAV 與基金級別尚待獨立核對；不使用來源淨值總額當單位淨值。"));
  }
  card.append(btn);
  return card;
}
async function catalogPage({reset=false,refresh=false}={}){
  if(catalog.busy)return;
  if(reset){
    catalog.offset=0;
    catalog.query=$("#catalog-query").value.trim();
    catalog.kind=$("#catalog-kind").value;
  }
  catalog.busy=true;
  const more=$("#catalog-more"), cards=$("#catalog-cards"),state=$("#catalog-state");
  more.disabled=true;$("#catalog-refresh").disabled=true;
  state.textContent="正在讀取公開市場名錄，首次載入可能較久…";
  const abort=new AbortController();
  const timer=setTimeout(()=>abort.abort(),38000);
  try{
    const url=new URL("/api/catalog",location.origin);
    url.searchParams.set("query",catalog.query);
    url.searchParams.set("kind",catalog.kind);
    url.searchParams.set("offset",String(catalog.offset));
    url.searchParams.set("limit","30");
    if(refresh)url.searchParams.set("refresh","1");
    const response=await fetch(url.toString(),{cache:"no-store",credentials:"omit",signal:abort.signal});
    const data=await response.json();
    if(!response.ok)throw Error(data.error||"名錄讀取失敗");
    if(data.schema!=="fiveline_adviser_catalog_search_v1" ||
       data.status!=="PUBLIC_DIRECTORY_ONLY" || data.real_prices_available!==false)
       throw Error("市場來源安全狀態未驗證");
    if(reset)cards.replaceChildren();
    data.items.forEach(row=>cards.append(renderCatalogItem(row)));
    catalog.offset+=data.items.length;
    catalog.matched=data.matched_rows;
    $("#catalog-total").textContent=Number(data.total_catalog_rows).toLocaleString("zh-TW");
    $("#catalog-matched").textContent=Number(data.matched_rows).toLocaleString("zh-TW");
    const checked=new Date(data.checked_at_utc);
    $("#catalog-asof").textContent=isNaN(checked.valueOf())?"日期未核對":checked.toLocaleDateString("zh-TW",{timeZone:"Asia/Taipei"});
    state.textContent=data.matched_rows?
      "目前顯示 "+catalog.offset+"／"+data.matched_rows+" 筆來源紀錄；名錄不含已核准歷史 NAV。":
      "找不到符合條件的已收錄標的。可改用其他名稱、代碼或切換類別。";
    more.hidden=catalog.offset>=data.matched_rows;
  }catch(err){
    state.textContent=err.name==="AbortError"?"公開名錄載入逾時，請稍後重試。":
                      "名錄暫時無法載入："+err.message;
    if(reset){cards.replaceChildren();more.hidden=true;}
  }finally{
    clearTimeout(timer);
    catalog.busy=false;
    more.disabled=false;$("#catalog-refresh").disabled=false;
  }
}
function ensureCatalog(){
  if(catalog.initialized)return;
  catalog.initialized=true;
  $("#catalog-search-form").addEventListener("submit",e=>{
    e.preventDefault();catalogPage({reset:true});
  });
  $("#catalog-refresh").addEventListener("click",()=>catalogPage({reset:true,refresh:true}));
  $("#catalog-more").addEventListener("click",()=>catalogPage());
  $("#catalog-selected").addEventListener("click",e=>{
    const btn=e.target.closest("[data-remove-uid]");
    if(!btn)return;
    catalog.selected.delete(btn.dataset.removeUid);
    renderCatalogSelection();
    // Refresh the visible per-item disabled states without a network fetch.
    document.querySelectorAll(".catalog-record button[data-add-uid]").forEach(button=>{
      const exists=catalog.selected.has(button.dataset.addUid);
      button.disabled=exists;
      button.textContent=exists?"已加入候選":"加入候選觀察";
    });
  });
  catalogPage({reset:true});
}
