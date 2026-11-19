(() => {
"use strict";
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
const CFG = window.APP_CONFIG || {};
const API = (CFG.API_BASE_URL || "http://127.0.0.1:8000").replace(/\/+$/,"");
const state = {token:localStorage.getItem("medisync_token")||"", user:null, org:null, page:"overview", cache:{}};
const roleInfo = {
  admin:{label:"Admin / Hospital Admin", nav:[["overview","Overview","⌂"],["organizations","Organizations","▣"],["users","Users","♙"],["inventory","Inventory","▦"],["medicines","Medicine catalogue","✚"],["requests","Medicine requests","⇄"],["transfers","Stock transfers","⇆"],["purchase-orders","Purchase orders","▤"],["suppliers","Suppliers","♧"],["notifications","Notifications","◉"],["audit-logs","Audit logs","▥"]]},
  inventory_manager:{label:"Inventory Manager", nav:[["overview","Overview","⌂"],["inventory","Inventory","▦"],["medicines","Medicine catalogue","✚"],["batches","Batches & expiry","◷"],["stock-transactions","Stock movement","⇅"],["requests","Medicine requests","⇄"],["transfers","Stock transfers","⇆"],["notifications","Notifications","◉"]]},
  doctor:{label:"Doctor / Hospital Staff", nav:[["overview","Overview","⌂"],["medicines","Search medicines","⌕"],["inventory","Availability","▦"],["requests","Medicine requests","⇄"],["notifications","Notifications","◉"]]},
  staff:{label:"Staff / Pharmacist", nav:[["overview","Overview","⌂"],["medicines","Medicine catalogue","✚"],["inventory","Inventory","▦"],["batches","Batches & expiry","◷"],["stock-transactions","Stock movement","⇅"],["requests","Medicine requests","⇄"],["notifications","Notifications","◉"]]},
  procurement:{label:"Procurement / Supplier", nav:[["overview","Overview","⌂"],["suppliers","Suppliers","♧"],["purchase-orders","Purchase orders","▤"],["inventory","Inventory","▦"],["notifications","Notifications","◉"]]}
};
const titles={"overview":"Dashboard","organizations":"Organizations","users":"User management","inventory":"Medicine inventory","medicines":"Medicine catalogue","batches":"Batch & expiry tracking","stock-transactions":"Stock movement history","requests":"Medicine requests","transfers":"Stock transfers","purchase-orders":"Purchase orders","suppliers":"Suppliers","notifications":"Notifications","audit-logs":"Audit logs"};
const esc=v=>String(v??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const query=o=>new URLSearchParams(Object.entries(o||{}).filter(([,v])=>v!==undefined&&v!==null&&v!=="")).toString();
function toast(msg,bad=false){const el=$("#toast");el.textContent=msg;el.className="toast show"+(bad?" error":"");clearTimeout(toast.t);toast.t=setTimeout(()=>el.className="toast",3600);}
function message(msg,bad=true){$("#authMessage").textContent=msg;$("#authMessage").style.color=bad?"var(--red)":"var(--mint)";}
async function api(path,opts={}){
  const headers={...(opts.body?{"Content-Type":"application/json"}:{}),...(state.token?{Authorization:`Bearer ${state.token}`}:{})};
  let res;
  try{res=await fetch(API+path,{...opts,headers,signal:AbortSignal.timeout(CFG.REQUEST_TIMEOUT_MS||20000)});}
  catch(e){throw new Error(`Cannot connect to ${API}. Start the backend and check config.js / CORS.`);}
  const ct=res.headers.get("content-type")||"";
  const data=ct.includes("application/json")?await res.json():await res.text();
  if(!res.ok)throw new Error(data?.detail||data?.message||data?.error||`API error ${res.status}`);
  return data;
}
function setAuthTab(tab){
  $$("[data-auth-tab]").forEach(b=>b.classList.toggle("active",b.dataset.authTab===tab));
  $("#loginForm").classList.toggle("hidden",tab!=="login");
  $("#registerForm").classList.toggle("hidden",tab!=="register");
  $("#organizationForm").classList.toggle("hidden",tab!=="organization");
  message("");
}
$$("[data-auth-tab]").forEach(b=>b.addEventListener("click",()=>setAuthTab(b.dataset.authTab)));
async function checkHealth(){
  try{const d=await api("/api/health");$("#apiDot").classList.add("ok");$("#apiStatusText").textContent=d.database==="connected"?"Backend & database connected":"Backend reachable"; }
  catch(e){$("#apiDot").classList.remove("ok");$("#apiStatusText").textContent="Backend not reachable — start FastAPI";}
}
$("#loginForm").addEventListener("submit",async e=>{
  e.preventDefault();const b=$('button[type="submit"]',e.currentTarget);b.disabled=true;
  try{const d=await api("/api/auth/login",{method:"POST",body:JSON.stringify({email:$("#loginEmail").value.trim(),password:$("#loginPassword").value})});
    state.token=d.access_token;state.user=d.user;localStorage.setItem("medisync_token",state.token);localStorage.setItem("medisync_user",JSON.stringify(state.user));
    enterApp();await loadPage("overview");
  }catch(err){message(err.message);}finally{b.disabled=false;}
});

$("#registerForm").addEventListener("submit", async e => {
    e.preventDefault();

    // Save the form reference before the API request
    const form = e.currentTarget;
    const d = Object.fromEntries(new FormData(form).entries());

    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;

    try {
        const r = await api("/api/auth/register", {
            method: "POST",
            body: JSON.stringify(d)
        });

        message(
            `Account created successfully. User ID: ${r.user_id}. You can now sign in.`,
            false
        );

        form.reset();

    } catch (err) {
        message(err.message, true);
    } finally {
        button.disabled = false;
    }
});

$("#organizationForm").addEventListener("submit",async e=>{
  e.preventDefault();const d=Object.fromEntries(new FormData(e.currentTarget).entries());
  try{const r=await api("/api/organizations",{method:"POST",body:JSON.stringify(d)});const id=r.id||r._id;$("#organizationResult").classList.remove("hidden");$("#organizationResult").innerHTML=`<b>Organization created successfully</b><br>Name: ${esc(r.name)}<br>Organization ID: <strong>${esc(id)}</strong><br><button class="mini-btn" id="copyOrgId" type="button">Copy organization ID</button><br><small>Use this ID in Create account. Save it safely.</small>`;$("#copyOrgId").onclick=()=>navigator.clipboard.writeText(id).then(()=>toast("Organization ID copied."));}
  catch(err){message(err.message);}
});
function enterApp(){
  $("#authScreen").classList.add("hidden");$("#app").classList.remove("hidden");
  const u=state.user||{};const info=roleInfo[u.role]||{label:u.role||"User",nav:roleInfo.staff.nav};
  $("#userName").textContent=u.name||u.email||"User";$("#userRole").textContent=info.label;
  $("#userAvatar").textContent=(u.name||u.email||"U").charAt(0).toUpperCase();
  $("#orgName").textContent=u.organization_id||"Organization workspace";$("#orgCode").textContent="ID "+(u.organization_id||"not supplied");
  $("#sideNav").innerHTML=info.nav.map(([id,label,ico])=>`<button class="nav-link ${state.page===id?"active":""}" data-page="${id}"><span>${ico}</span>${label}</button>`).join("");
  $("#sideNav").onclick=e=>{const b=e.target.closest("[data-page]");if(b)loadPage(b.dataset.page);};
}
$("#logoutBtn").onclick=()=>{state.token="";state.user=null;localStorage.removeItem("medisync_token");localStorage.removeItem("medisync_user");$("#app").classList.add("hidden");$("#authScreen").classList.remove("hidden");setAuthTab("login");toast("Signed out.");};
$("#mobileMenu").onclick=()=>$("#sidebar").classList.toggle("open");
function orgQuery(){return state.user?.organization_id?`?${query({organization_id:state.user.organization_id})}`:"";}
function status(s){const v=String(s||"unknown").toLowerCase();let c=/approved|active|available|completed|received|confirmed/.test(v)?"good":/reject|expired|cancel|out_of_stock/.test(v)?"bad":/pending|low|transit|draft|submitted/.test(v)?"warn":"";return `<span class="status ${c}">${esc(v.replaceAll("_"," "))}</span>`;}
function table(headers,rows,empty="No records found."){return `<div class="panel table-panel"><div class="table-wrap"><table><thead><tr>${headers.map(x=>`<th>${esc(x)}</th>`).join("")}</tr></thead><tbody>${rows.length?rows.join(""):`<tr><td colspan="${headers.length}"><div class="empty">${esc(empty)}</div></td></tr>`}</tbody></table></div></div>`;}
function stat(label,value,foot,ico){return `<div class="stat-card"><span class="stat-icon">${ico}</span><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div><div class="stat-foot">${esc(foot)}</div></div>`;}
function panel(title,sub,content){return `<section class="panel"><div class="panel-head"><div><h3>${title}</h3><div class="panel-sub">${sub}</div></div></div>${content}</section>`;}
async function loadPage(page){
  state.page=page;$("#pageCrumb").textContent=titles[page]||page;$("#pageHeading").innerHTML=`<div><h1>${titles[page]||page}</h1><p>Live records from the MEDI-SYNC backend.</p></div><div class="heading-actions">${page==="medicines"?'<button class="btn primary" data-action="add-medicine">＋ Add medicine</button>':""}${page==="inventory"?'<button class="btn primary" data-action="stock-in">＋ Receive stock</button> <button class="btn secondary" data-action="stock-out">− Issue stock</button>':""}${page==="requests"&&state.user.role!=="doctor"?'<button class="btn secondary" data-action="process-priority">Process priority queue</button>':""}</div>`;
  $$(".nav-link").forEach(b=>b.classList.toggle("active",b.dataset.page===page));
  $("#pageContent").innerHTML='<div class="loading-inline">Loading live records…</div>';
  try{
    let html="";
    if(page==="overview")html=await overview();
    else if(page==="organizations")html=await organizations();
    else if(page==="users")html=await users();
    else if(page==="medicines")html=await medicines();
    else if(page==="inventory")html=await inventory();
    else if(page==="batches")html=await batches();
    else if(page==="stock-transactions")html=await transactions();
    else if(page==="requests")html=await requests();
    else if(page==="transfers")html=await transfers();
    else if(page==="purchase-orders")html=await purchaseOrders();
    else if(page==="suppliers")html=await suppliers();
    else if(page==="notifications")html=await notifications();
    else if(page==="audit-logs")html=await auditLogs();
    $("#pageContent").innerHTML=html;
  }catch(err){$("#pageContent").innerHTML=`<div class="panel"><h3>Could not load this section</h3><p class="muted">${esc(err.message)}</p><button class="btn secondary" id="retryPage">Try again</button></div>`;$("#retryPage").onclick=()=>loadPage(page);}
}
async function overview(){
  const [s,activity,inv]=await Promise.all([api("/api/dashboard/summary"+orgQuery()),api("/api/dashboard/recent-activity?limit=8"),api("/api/inventory"+orgQuery())]);
  const recent=activity.map(x=>`<tr><td>${esc(x.action)}</td><td>${esc(x.entity)}</td><td>${esc(x.performed_by)}</td><td>${esc(fmt(x.created_at))}</td></tr>`);
  const low=inv.filter(x=>x.stock_status==="low_stock"||x.stock_status==="out_of_stock").slice(0,6);
  return `<div class="grid stats-grid">${stat("Inventory records",s.inventory_records,"Tracked medicine / organization pairs","▦")}${stat("Total stock units",s.total_units,"Current recorded quantity","＋")}${stat("Low stock items",s.low_stock_items,"At or below minimum stock","!")}${stat("Pending requests",s.pending_requests,"Awaiting review","⇄")}</div><div class="grid two-col">${panel("Supply snapshot","Organization inventory signals",`<div class="quick-list"><div class="quick-item">Active medicines <b>${esc(s.active_medicines)}</b></div><div class="quick-item">Out of stock <b>${esc(s.out_of_stock_items)}</b></div><div class="quick-item">Pending transfers <b>${esc(s.pending_transfers)}</b></div><div class="quick-item">Unread notifications <b>${esc(s.unread_notifications)}</b></div><div class="quick-item">Organizations <b>${esc(s.organizations)}</b></div></div>`)}${panel("Attention required","Low and out-of-stock records",low.length?`<div class="quick-list">${low.map(x=>`<div class="quick-item"><div><b>${esc(x.medicine_name)}</b><small>${esc(x.organization_name)}</small></div>${status(x.stock_status)} <strong>${esc(x.quantity)}</strong></div>`).join("")}</div>`:'<div class="empty">No low-stock records returned.</div>')}</div>${panel("Recent platform activity","Latest audit log events",table(["Action","Entity","Performed by","Date"],recent,"No audit activity yet."))}<div class="notice"><strong>AI integration:</strong> The ZIP includes a separate Flask AI service with forecast, shortage-risk, expiry-risk and recommendations endpoints. It is not the same server as this FastAPI backend; the integration instructions are in README.</div>`;
}
async function organizations(){const rows=await api("/api/organizations?active_only=false");return `<div class="toolbar"><button class="btn primary" data-action="create-organization">＋ Add organization</button></div>${table(["Organization","Code","Type","Email","Status","Created"],rows.map(x=>`<tr><td><b>${esc(x.name)}</b><small>${esc(x.address)}</small></td><td>${esc(x.organization_code)}</td><td>${esc(x.organization_type)}</td><td>${esc(x.email)}</td><td>${status(x.status)}</td><td>${esc(fmt(x.created_at))}</td></tr>`))}`;}
async function users(){const rows=await api("/api/users"+orgQuery());return table(["Name","Email","Backend role","Organization","Status"],rows.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.email)}</td><td>${esc(x.role)}</td><td>${esc(x.organization_id)}</td><td>${status(x.status)}</td></tr>`));}
async function medicines(){const rows=await api("/api/medicines");return `<div class="toolbar"><input id="tableSearch" placeholder="Search medicine name or code"><button class="btn secondary" id="searchRows">Filter</button><button class="btn primary" data-action="add-medicine">＋ Add medicine</button></div>${table(["Medicine","Code","Composition","Category","Unit","Status",""],rows.map(x=>`<tr><td><b>${esc(x.name)}</b><small>${esc(x.description)}</small></td><td>${esc(x.medicine_code)}</td><td>${esc(x.composition)}</td><td>${esc(x.category)}</td><td>${esc(x.unit)}</td><td>${status(x.status)}</td><td><button class="mini-btn" data-edit-medicine="${esc(x.id)}">Edit</button></td></tr>`))}`;}
async function inventory(){const rows=await api("/api/inventory"+orgQuery());return `<div class="toolbar"><input id="tableSearch" placeholder="Search medicine / organization"><select id="stockFilter"><option value="">All stock states</option><option value="available">Available</option><option value="low_stock">Low stock</option><option value="out_of_stock">Out of stock</option></select><button class="btn secondary" id="searchRows">Filter</button><button class="btn primary" data-action="create-inventory">＋ Add inventory record</button><button class="btn secondary" data-action="stock-in">Receive stock</button><button class="btn secondary" data-action="stock-out">Issue stock</button></div>${table(["Medicine","Organization","Quantity","Minimum","Reorder level","Location","Stock status"],rows.map(x=>`<tr><td><b>${esc(x.medicine_name)}</b><small>${esc(x.medicine_id)}</small></td><td>${esc(x.organization_name)}</td><td><b>${esc(x.quantity)}</b></td><td>${esc(x.minimum_stock)}</td><td>${esc(x.reorder_level)}</td><td>${esc(x.location)}</td><td>${status(x.stock_status)}</td></tr>`))}`;}
async function batches(){const rows=await api("/api/batches"+orgQuery());return table(["Medicine ID","Batch","Quantity","Manufactured","Expiry","Status"],rows.map(x=>`<tr><td>${esc(x.medicine_id)}</td><td>${esc(x.batch_number)}</td><td>${esc(x.quantity)}</td><td>${esc(fmt(x.manufacturing_date))}</td><td>${esc(fmt(x.expiry_date))}</td><td>${status(x.status)}</td></tr>`));}
async function transactions(){const rows=await api("/api/stock-transactions"+orgQuery());return table(["Type","Medicine","Quantity","Batch","Reason / reference","Performed by","Date"],rows.map(x=>`<tr><td>${esc(x.transaction_type)}</td><td>${esc(x.medicine_id)}</td><td>${esc(x.quantity)}</td><td>${esc(x.batch_number||"—")}</td><td>${esc(x.reason||x.reference)}</td><td>${esc(x.performed_by)}</td><td>${esc(fmt(x.created_at))}</td></tr>`));}
async function requests(){const rows=await api("/api/medicine-requests"+orgQuery());return table(["Medicine","Quantity","Urgency","Requested by","Status","Notes","Actions"],rows.map(x=>`<tr><td>${esc(x.medicine_id)}</td><td>${esc(x.quantity)}</td><td>${status(x.urgency)}</td><td>${esc(x.requested_by)}</td><td>${status(x.status)}</td><td>${esc(x.notes)}</td><td>${x.status==="pending"&&state.user.role!=="doctor"?`<button class="mini-btn" data-decision="${esc(x.id)}" data-value="approve">Approve</button> <button class="mini-btn red" data-decision="${esc(x.id)}" data-value="reject">Reject</button>`:"—"}</td></tr>`));}
async function transfers(){const rows=await api("/api/transfers"+orgQuery());return `<div class="toolbar"><button class="btn primary" data-action="create-transfer">＋ Request transfer</button></div>${table(["Medicine","From organization","To organization","Quantity","Requested by","Status","Actions"],rows.map(x=>`<tr><td>${esc(x.medicine_id)}</td><td>${esc(x.from_organization_id)}</td><td>${esc(x.to_organization_id)}</td><td>${esc(x.quantity)}</td><td>${esc(x.requested_by)}</td><td>${status(x.status)}</td><td>${x.status==="pending_approval"?`<button class="mini-btn" data-transfer="${esc(x.id)}" data-value="approve">Approve</button> <button class="mini-btn red" data-transfer="${esc(x.id)}" data-value="reject">Reject</button>`:x.status==="approved"?`<button class="mini-btn" data-transfer="${esc(x.id)}" data-value="dispatch">Dispatch</button>`:x.status==="in_transit"?`<button class="mini-btn" data-transfer="${esc(x.id)}" data-value="receive">Receive</button>`:"—"}</td></tr>`))}`;}
async function purchaseOrders(){const rows=await api("/api/purchase-orders"+orgQuery());return `<div class="toolbar"><button class="btn primary" data-action="create-po">＋ Create purchase order</button></div>${table(["Order","Supplier","Items","Total (INR)","Status","Created by","Update"],rows.map(x=>`<tr><td>${esc(x.id)}</td><td>${esc(x.supplier_id)}</td><td>${esc((x.items||[]).length)} line(s)</td><td>₹${esc(x.total_amount)}</td><td>${status(x.status)}</td><td>${esc(x.created_by)}</td><td><button class="mini-btn" data-po="${esc(x.id)}">Update</button></td></tr>`))}`;}
async function suppliers(){const rows=await api("/api/suppliers");return `<div class="toolbar"><button class="btn primary" data-action="add-supplier">＋ Add supplier</button></div>${table(["Supplier","Contact person","Email","Phone","Address","Status"],rows.map(x=>`<tr><td><b>${esc(x.name)}</b></td><td>${esc(x.contact_person)}</td><td>${esc(x.email)}</td><td>${esc(x.phone)}</td><td>${esc(x.address)}</td><td>${status(x.status)}</td></tr>`))}`;}
async function notifications(){const rows=await api("/api/notifications"+orgQuery());return table(["Alert","Message","Type","Severity","Read","Date","Action"],rows.map(x=>`<tr><td><b>${esc(x.title)}</b></td><td>${esc(x.message)}</td><td>${esc(x.type)}</td><td>${status(x.severity)}</td><td>${x.is_read?"Yes":"No"}</td><td>${esc(fmt(x.created_at))}</td><td>${!x.is_read?`<button class="mini-btn" data-read="${esc(x.id)}">Mark read</button>`:"—"}</td></tr>`));}
async function auditLogs(){const rows=await api("/api/audit-logs"+orgQuery());return table(["Action","Entity","Entity ID","Performed by","Organization","Date"],rows.map(x=>`<tr><td>${esc(x.action)}</td><td>${esc(x.entity)}</td><td>${esc(x.entity_id)}</td><td>${esc(x.performed_by)}</td><td>${esc(x.organization_id)}</td><td>${esc(fmt(x.created_at))}</td></tr>`));}
function fmt(v){if(!v)return "—";const d=new Date(v);return isNaN(d)?String(v):d.toLocaleString();}
function modal(title,fields,submit,handler){
  $("#modalRoot").innerHTML=`<div class="modal-backdrop"><section class="modal"><div class="modal-head"><h2>${esc(title)}</h2><button class="close-btn" data-close>×</button></div><form id="actionForm"><div class="form-grid">${fields.map(f=>`<label class="field ${f.wide?"wide":""}">${esc(f.label)}${f.options?`<select name="${f.name}" ${f.required?"required":""}>${f.options.map(o=>`<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("")}</select>`:f.textarea?`<textarea name="${f.name}" rows="2" ${f.required?"required":""} placeholder="${esc(f.placeholder||"")}"></textarea>`:`<input name="${f.name}" type="${f.type||"text"}" ${f.required?"required":""} ${f.min!==undefined?`min="${f.min}"`:""} placeholder="${esc(f.placeholder||"")}">`}</label>`).join("")}</div><div class="modal-actions"><button type="button" class="btn secondary" data-close>Cancel</button><button class="btn primary" type="submit">${esc(submit)}</button></div><div id="modalError" class="message"></div></form></section></div>`;
  $$("[data-close]",$("#modalRoot")).forEach(b=>b.onclick=()=>$("#modalRoot").innerHTML="");
  $("#actionForm").onsubmit=async e=>{e.preventDefault();const b=$('button[type="submit"]',e.currentTarget);b.disabled=true;try{await handler(Object.fromEntries(new FormData(e.currentTarget).entries()));$("#modalRoot").innerHTML="";toast("Saved successfully.");loadPage(state.page);}catch(err){$("#modalError").textContent=err.message;}finally{b.disabled=false;}};
}
const org=()=>state.user?.organization_id||"";
const actor=()=>state.user?.name||state.user?.email||"current-user";
function action(id){
 if(id==="create-organization")return modal("Register organization",[{name:"name",label:"Organization name",required:true},{name:"organization_code",label:"Unique organization code",required:true},{name:"organization_type",label:"Type",options:["hospital","pharmacy","clinic","warehouse"].map(x=>({value:x,label:x}))},{name:"email",label:"Email"},{name:"phone",label:"Phone"},{name:"address",label:"Address",wide:true}], "Create organization",d=>api("/api/organizations",{method:"POST",body:JSON.stringify(d)}));
 if(id==="add-medicine")return modal("Add medicine",[{name:"medicine_code",label:"Medicine code",required:true},{name:"name",label:"Medicine name",required:true},{name:"composition",label:"Composition"},{name:"category",label:"Category"},{name:"unit",label:"Unit"},{name:"description",label:"Description",wide:true}], "Save medicine",d=>api("/api/medicines",{method:"POST",body:JSON.stringify(d)}));
 if(id==="create-inventory")return modal("Create inventory record",[{name:"organization_id",label:"Organization ID",required:true,placeholder:org()},{name:"medicine_id",label:"Medicine ID",required:true},{name:"minimum_stock",label:"Minimum stock",type:"number",min:0},{name:"reorder_level",label:"Reorder level",type:"number",min:0},{name:"location",label:"Storage location"}],"Create record",d=>api("/api/inventory",{method:"POST",body:JSON.stringify({...d,minimum_stock:+d.minimum_stock||0,reorder_level:+d.reorder_level||0})}));
 if(id==="stock-in")return modal("Receive stock (stock-in)",[{name:"organization_id",label:"Organization ID",required:true,placeholder:org()},{name:"medicine_id",label:"Medicine ID",required:true},{name:"quantity",label:"Quantity",type:"number",min:1,required:true},{name:"batch_number",label:"Batch number",required:true},{name:"manufacturing_date",label:"Manufacturing date",type:"date"},{name:"expiry_date",label:"Expiry date",type:"date",required:true},{name:"supplier_id",label:"Supplier ID"},{name:"reference",label:"Reference"},{name:"performed_by",label:"Recorded by",required:true,placeholder:actor()}],"Receive stock",d=>api("/api/stock-in",{method:"POST",body:JSON.stringify({...d,quantity:+d.quantity,manufacturing_date:d.manufacturing_date||null,supplier_id:d.supplier_id||null,performed_by:d.performed_by||actor()})}));
 if(id==="stock-out")return modal("Issue stock (stock-out)",[{name:"organization_id",label:"Organization ID",required:true,placeholder:org()},{name:"medicine_id",label:"Medicine ID",required:true},{name:"quantity",label:"Quantity",type:"number",min:1,required:true},{name:"reason",label:"Reason",required:true},{name:"reference",label:"Reference"},{name:"performed_by",label:"Recorded by",required:true,placeholder:actor()}],"Issue stock",d=>api("/api/stock-out",{method:"POST",body:JSON.stringify({...d,quantity:+d.quantity,performed_by:d.performed_by||actor()})}));
 if(id==="create-transfer")return modal("Request stock transfer",[{name:"medicine_id",label:"Medicine ID",required:true},{name:"from_organization_id",label:"From organization ID",required:true},{name:"to_organization_id",label:"To organization ID",required:true},{name:"quantity",label:"Quantity",type:"number",min:1,required:true},{name:"requested_by",label:"Requested by",required:true,placeholder:actor()},{name:"notes",label:"Notes",wide:true}], "Submit transfer",d=>api("/api/transfers",{method:"POST",body:JSON.stringify({...d,quantity:+d.quantity})}));
 if(id==="add-supplier")return modal("Add supplier",[{name:"name",label:"Supplier name",required:true},{name:"contact_person",label:"Contact person"},{name:"email",label:"Email",type:"email"},{name:"phone",label:"Phone"},{name:"address",label:"Address",wide:true}],"Save supplier",d=>api("/api/suppliers",{method:"POST",body:JSON.stringify(d)}));
 if(id==="create-po")return modal("Create purchase order",[{name:"organization_id",label:"Organization ID",required:true,placeholder:org()},{name:"supplier_id",label:"Supplier ID",required:true},{name:"medicine_id",label:"Medicine ID",required:true},{name:"quantity",label:"Quantity",type:"number",min:1,required:true},{name:"unit_price",label:"Unit price (INR)",type:"number",min:0,required:true},{name:"created_by",label:"Created by",required:true,placeholder:actor()},{name:"notes",label:"Notes",wide:true}],"Create purchase order",d=>api("/api/purchase-orders",{method:"POST",body:JSON.stringify({organization_id:d.organization_id,supplier_id:d.supplier_id,items:[{medicine_id:d.medicine_id,quantity:+d.quantity,unit_price:+d.unit_price}],created_by:d.created_by||actor(),notes:d.notes})}));
 if(id==="process-priority")return api(`/api/medicine-requests/process-priority?${query({organization_id:org(),reviewed_by:actor()})}`,{method:"POST"}).then(d=>{toast(`Processed ${d.processed_count} request(s).`);loadPage("requests");}).catch(e=>toast(e.message,true));
}
document.addEventListener("click",async e=>{
 const a=e.target.closest("[data-action]");if(a){action(a.dataset.action);return;}
 const edit=e.target.closest("[data-edit-medicine]");if(edit){const id=edit.dataset.editMedicine;return modal("Update medicine",[{name:"name",label:"Medicine name"},{name:"composition",label:"Composition"},{name:"category",label:"Category"},{name:"unit",label:"Unit"},{name:"description",label:"Description",wide:true}],"Update medicine",d=>api(`/api/medicines/${encodeURIComponent(id)}`,{method:"PUT",body:JSON.stringify(d)}));}
 const dec=e.target.closest("[data-decision]");if(dec){const notes=prompt("Review notes (optional):")||"";try{await api(`/api/medicine-requests/${dec.dataset.decision}/decision`,{method:"PUT",body:JSON.stringify({decision:dec.dataset.value,reviewed_by:actor(),notes})});toast("Request updated.");loadPage("requests");}catch(err){toast(err.message,true);}return;}
 const tr=e.target.closest("[data-transfer]");if(tr){const notes=prompt("Notes (optional):")||"";try{await api(`/api/transfers/${tr.dataset.transfer}/action`,{method:"PUT",body:JSON.stringify({action:tr.dataset.value,performed_by:actor(),notes})});toast("Transfer updated.");loadPage("transfers");}catch(err){toast(err.message,true);}return;}
 const po=e.target.closest("[data-po]");if(po){const s=prompt("Enter status: submitted, confirmed, partially_received, received, cancelled","confirmed");if(!s)return;try{await api(`/api/purchase-orders/${po.dataset.po}/status?${query({new_status:s,performed_by:actor()})}`,{method:"PUT"});toast("Purchase order updated.");loadPage("purchase-orders");}catch(err){toast(err.message,true);}return;}
 const read=e.target.closest("[data-read]");if(read){try{await api(`/api/notifications/${read.dataset.read}`,{method:"PUT",body:JSON.stringify({is_read:true})});toast("Notification marked as read.");loadPage("notifications");}catch(err){toast(err.message,true);}return;}
 if(e.target.id==="searchRows"){const q=($("#tableSearch")?.value||"").toLowerCase();const stock=$("#stockFilter")?.value||"";$$("tbody tr").forEach(r=>r.hidden=(!r.textContent.toLowerCase().includes(q))||(stock&&!r.textContent.toLowerCase().includes(stock.replaceAll("_"," "))))}
});
async function restore(){const saved=localStorage.getItem("medisync_user");if(!state.token||!saved)return;try{state.user=JSON.parse(saved);enterApp();await loadPage("overview");}catch(e){state.token="";localStorage.removeItem("medisync_token");localStorage.removeItem("medisync_user");}}
window.addEventListener("load",()=>setTimeout(()=>$("#loader")?.classList.add("hide"),2400));
checkHealth();restore();
})();