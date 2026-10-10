import {initialState,applySnapshot,applyEvent,sseParser,ageData,aggregate} from '/demo/state.mjs';
import {el,button,empty,errorBox,formatLevel,formatTime,short,unit,latest,condition,statusBadges,badge,deviceLabel,deviceReporting} from './ui.mjs';
import {mountManagement} from './management.mjs';
import {deviceConnection,deviceReadingStatus,summarizeLocationConnection,startDeviceContactMonitor} from './device-health.mjs';
import {mountView} from './views.mjs';
import {liveChanges,addLiveEvent,mergeChanges,CONFIGURATION_REASONS} from './refresh.mjs';
const $=id=>document.getElementById(id), state=initialState();
let token='',controller=null,generation=0,routeController=null,routeCleanup=null,overview=null,staleSeconds=30,serverOffset=0,metadataTimer=null,liveTimer=null,refreshing=null,metadataDirty=false;
const subscriptions=new Set();
let pendingLive=liveChanges(),pendingMetadata=liveChanges();
const isLocalHost=['localhost','127.0.0.1','[::1]'].includes(location.hostname);
let localAccess=false, sessionPromise=null, activeRoute=null;
function accessHeaders(){return localAccess?{'X-Soundwatch-Local':'1'}:token?{Authorization:`Bearer ${token}`}:{}}
async function localSession(signal){
 if(sessionPromise)return sessionPromise;
 const pending=(async()=>{
  const response=await fetch('/app/session',{method:'POST',credentials:'same-origin',headers:{'X-Soundwatch-Local':'1','Content-Type':'application/json'},body:'{}',cache:'no-store',signal});
  if(!response.ok)throw new Error('The local workspace could not open. Keep Docker running and try again.');
  const result=await response.json();
  if(result.access!=='local')throw new Error('The server could not open this local workspace.');
 })();
 sessionPromise=pending;
 try{await pending;}finally{if(sessionPromise===pending)sessionPromise=null;}
}
function startupError(message){$('startup').hidden=false;$('login').hidden=true;$('startup-error').replaceChildren(errorBox(message));$('retry-local').hidden=false;}
const ctx={state,locations:[],devices:[],serverNow:()=>Date.now()+serverOffset,dataStaleSeconds:()=>staleSeconds,api,audioFile:(id,options={})=>api(`/audio/${encodeURIComponent(id)}/file`,{...options,responseType:'blob'}),navigate:hash=>{location.hash=hash},refresh:refreshMetadata,reportLiveError:()=>connection('Some view data could not refresh','reconnecting'),onLive:fn=>{subscriptions.add(fn);return()=>subscriptions.delete(fn)},signal:null};
async function api(path,options={},retried=false){
 const {responseType,...requestOptions}=options;
 const response=await fetch(path,{cache:'no-store',credentials:'same-origin',...requestOptions,headers:{...accessHeaders(),...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});
 if(localAccess&&response.status===401&&!retried){await localSession(controller?.signal);return api(path,options,true);}
 if(response.ok&&responseType==='blob')return response.blob();
 const data=await response.json().catch(()=>null);
 if(!response.ok){const detail=data?.error;const message=typeof detail?.message==='string'?detail.message:detail?.message?.message;let text=response.status===401||response.status===403?(localAccess?'The local connection needs to be reopened. Use Reconnect.':'Administrator access was not accepted. Please reconnect.'):message||`Request failed (${response.status}). Please try again.`;if(detail?.details?.length)text+=' '+detail.details.map(d=>`${(d.location||[]).slice(1).join(' ')}: ${d.message}`).join('; ');const err=new Error(text);err.status=response.status;throw err;}return data;
}
async function allPages(path,signal){const items=[];let offset=0;while(true){const page=await api(`${path}${path.includes('?')?'&':'?'}limit=200&offset=${offset}`,{signal});items.push(...page.items);offset+=page.items.length;if(offset>=page.total||!page.items.length)return items;}}
async function refreshMetadata(){
 const signal=controller?.signal,run=generation;
 if(!signal||signal.aborted)return;
 metadataDirty=true;
 if(refreshing)return refreshing;
 const pending=(async()=>{
  while(metadataDirty&&!signal.aborted&&run===generation){
   metadataDirty=false;
   const[locations,devices]=await Promise.all([allPages('/locations',signal),allPages('/devices',signal)]);
   const rules=await Promise.all(locations.map(async l=>{const t=await api(`/locations/${l.id}/threshold`,{signal});return{...l,current_threshold:t.current}}));
   if(signal.aborted||run!==generation)return;
   ctx.locations=rules;ctx.devices=devices;
   const openStreams=new Set([...state.incidents.values()].filter(i=>['active','recovering'].includes(i.status)).map(i=>i.stream_id));
   for(const meta of rules){const l=state.locations.get(meta.id);if(l){Object.assign(l,{name:meta.name,latitude:meta.latitude,longitude:meta.longitude});l.devices=devices.filter(d=>d.location_id===l.id).map(d=>({id:d.id,enabled:d.enabled,assignment_id:d.current_assignment_id}));l.streams=l.streams.filter(s=>openStreams.has(s.id)||l.devices.some(d=>d.id===s.device_id&&d.assignment_id===s.assignment_id));aggregate(l,state.incidents);}}
  }
 })();
 refreshing=pending;try{return await pending;}finally{if(refreshing===pending)refreshing=null;}
}
async function loadSnapshot(signal){let offset=0,first=null,items=[],incidents=new Map();while(true){const p=await api(`/locations/status?limit=200&offset=${offset}`,{signal});first??=p;items.push(...p.items);p.incidents.forEach(x=>incidents.set(x.id,x));offset+=p.items.length;if(offset>=p.total||!p.items.length)break;}staleSeconds=first.data_stale_seconds;serverOffset=Date.parse(first.as_of)-Date.now();applySnapshot(state,{...first,items,incidents:[...incidents.values()]});}
function connection(text,kind=''){$('connection').textContent=`● ${text}`;$('connection').className=`connection ${kind}`;}
function liveUpdate(event=null,changes=null){
 if(event)addLiveEvent(pendingLive,event);else if(changes)mergeChanges(pendingLive,changes);else pendingLive.aged=true;
 if(liveTimer)return;
 liveTimer=setTimeout(()=>{liveTimer=null;const batch=pendingLive;pendingLive=liveChanges();overview?.update();for(const callback of subscriptions)callback(batch);window.dispatchEvent(new CustomEvent('noise:state',{detail:batch}));},650);
}
function scheduleMetadata(event){
 addLiveEvent(pendingMetadata,event);clearTimeout(metadataTimer);
 const run=generation,signal=controller?.signal;
 metadataTimer=setTimeout(async()=>{
  if(signal?.aborted||run!==generation)return;
  const batch=pendingMetadata;pendingMetadata=liveChanges();
  try{await refreshMetadata();if(signal?.aborted||run!==generation)return;batch.metadata=true;liveUpdate(null,batch);}
  catch{if(!signal?.aborted&&run===generation)connection('Configuration refresh failed','reconnecting');}
 },500);
}
function disconnect(){generation++;controller?.abort();controller=null;routeController?.abort();routeCleanup?.();routeCleanup=null;overview?.destroy();overview=null;subscriptions.clear();token='';localAccess=false;sessionPromise=null;refreshing=null;metadataDirty=false;clearTimeout(metadataTimer);clearTimeout(liveTimer);liveTimer=null;pendingLive=liveChanges();pendingMetadata=liveChanges();$('content').replaceChildren();$('content').hidden=true;$('startup').hidden=!isLocalHost;$('login').hidden=isLocalHost;$('signout').hidden=true;$('reconnect').hidden=true;$('connect').disabled=false;connection('Not connected');}
async function connectLoop(signal,run){let needsSnapshot=true;let renewedStreamSession=false;let delay=1500;while(!signal.aborted&&run===generation){try{if(needsSnapshot){await Promise.all([refreshMetadata(),loadSnapshot(signal)]);if(signal.aborted)return;needsSnapshot=false;$('startup').hidden=true;$('login').hidden=true;$('content').hidden=false;$('signout').hidden=localAccess;$('reconnect').hidden=false;await renderRoute();}connection('Connecting');const response=await fetch('/events/stream',{headers:{...accessHeaders(),'Last-Event-ID':state.cursor},credentials:'same-origin',signal,cache:'no-store'});if([409,410].includes(response.status)){needsSnapshot=true;continue}if(localAccess&&response.status===401&&!renewedStreamSession){renewedStreamSession=true;await localSession(signal);continue}if([401,403].includes(response.status)){const err=new Error(localAccess?'The local connection needs to be reopened.':'Administrator access was not accepted.');err.status=response.status;throw err}if(!response.ok)throw new Error('Live connection interrupted');connection('Live updates','live');renewedStreamSession=false;delay=1500;const reader=response.body.getReader(),decoder=new TextDecoder();let resync=false;
 const parse=sseParser(frame=>{if(frame.type==='stream.resync_required'){resync=true;return}applyEvent(state,frame.data,frame.id);liveUpdate(frame.data);if(CONFIGURATION_REASONS.has(frame.data.transition_reason))scheduleMetadata(frame.data);});
 try{while(!signal.aborted){const{done,value}=await reader.read();if(done)break;parse(decoder.decode(value,{stream:true}));if(resync){needsSnapshot=true;await reader.cancel();break}}}finally{reader.releaseLock()}if(resync)continue;throw new Error('Live connection interrupted');
 }catch(error){if(signal.aborted||run!==generation)return;if([401,403].includes(error.status)){disconnect();if(isLocalHost)startupError('The local connection needs to be reopened. Please try again.');else $('login-error').replaceChildren(errorBox(error.message));return;}connection('Reconnecting…','reconnecting');if(isLocalHost&&!$('startup').hidden)startupError('Waiting for the local server. Keep Docker running.');else if(!$('login').hidden)$('login-error').replaceChildren(errorBox(error.message));await new Promise(resolve=>{const timer=setTimeout(resolve,delay);signal.addEventListener('abort',()=>{clearTimeout(timer);resolve()},{once:true})});delay=Math.min(delay*1.5,15000);}}}
$('login-form').onsubmit=e=>{e.preventDefault();const value=$('admin-token').value.trim();if(!value)return;disconnect();Object.assign(state,initialState());token=value;$('admin-token').value='';$('login-error').replaceChildren();$('connect').disabled=true;controller=new AbortController();connection('Loading workspace');void connectLoop(controller.signal,generation);};
async function startLocal(){
 disconnect();Object.assign(state,initialState());$('startup-error').replaceChildren();$('retry-local').hidden=true;controller=new AbortController();const signal=controller.signal,run=generation;connection('Opening local workspace');
 try{await localSession(signal);if(signal.aborted||run!==generation)return;localAccess=true;void connectLoop(signal,run);}
 catch(error){if(!signal.aborted&&run===generation){connection('Local connection interrupted','reconnecting');startupError(error.message);}}
}
$('retry-local').onclick=()=>void startLocal();
$('signout').onclick=disconnect;$('reconnect').onclick=()=>{if(isLocalHost){void startLocal();return;}controller?.abort();generation++;controller=new AbortController();refreshing=null;void connectLoop(controller.signal,generation)};window.addEventListener('pagehide',disconnect);
document.querySelector('.skip-link').addEventListener('click',e=>{e.preventDefault();$('main').focus();});
window.addEventListener('hashchange',()=>{if((localAccess||token)&&state.cursor)void renderRoute({navigation:true})});
setInterval(()=>{if((localAccess||token)&&state.cursor){const previous=JSON.stringify([...state.locations.values()].map(l=>[l.data_status,...l.streams.map(s=>s.data_status)]));ageData(state,Date.now()+serverOffset,staleSeconds);if(previous!==JSON.stringify([...state.locations.values()].map(l=>[l.data_status,...l.streams.map(s=>s.data_status)])))liveUpdate();}},1000);
async function renderRoute({navigation=false}={}){
 const hash=location.hash.slice(1).replace(/^\//,'');
 const [path,query='']=hash.split('?'),[page='overview',id]=path.split('/');
 const labels={overview:'Overview',location:'Location details',incidents:'Incident history',incident:'Incident details',reports:'Daily reports',management:'Management'};
 const key=labels[page]?page:'overview',identity=`${key}/${['location','incident'].includes(key)?id||'':''}`;
 const moveFocus=navigation&&identity!==activeRoute,focusOrigin=document.activeElement;
 activeRoute=identity;
 routeController?.abort();routeCleanup?.();routeCleanup=null;overview?.destroy();overview=null;
 routeController=new AbortController();ctx.signal=routeController.signal;const signal=ctx.signal;
 document.title=`${labels[key]} · UrbanEcho`;
 $('page-label').textContent=labels[key];
 document.querySelectorAll('nav a').forEach(a=>{const active=a.dataset.page===(key==='location'?'overview':key==='incident'?'incidents':key);a.classList.toggle('active',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current')});
 const container=$('content');container.replaceChildren();
 function focusPage(){
  // Only deliberate page navigation moves focus. A later interaction during
  // loading takes precedence, as do background reconnects and filter changes.
  if(!moveFocus||signal.aborted||![focusOrigin,document.body,$('main')].includes(document.activeElement))return;
  const target=container.querySelector('h1')||$('main');target.tabIndex=-1;target.focus();
 }
 try{
  if(key==='overview'){overview=mountOverview(container);focusPage();return;}
  const loading=el('p',`Loading ${labels[key].toLowerCase()}…`,'loading');loading.setAttribute('role','status');
  const target=el('div');target.hidden=true;container.append(loading,target);
  let cleanup;
  if(key==='management'){await refreshMetadata();if(signal.aborted)return;cleanup=await mountManagement(target,routeContext(signal));}
  else cleanup=await mountView(target,{page:key,id,params:new URLSearchParams(query)},routeContext(signal));
  if(signal.aborted){cleanup?.();return;}
  // Reveal the completed initial view once, avoiding a loading row above a
  // growing page that would disappear and shift every control on completion.
  loading.remove();target.hidden=false;routeCleanup=cleanup;focusPage();
 }catch(error){
  if(!signal.aborted){container.replaceChildren(errorBox(error.message),button('Try again',()=>renderRoute()));focusPage();}
 }
}
function routeContext(signal){return {...ctx,signal,get locations(){return ctx.locations},get devices(){return ctx.devices}};}
function mountOverview(container){
 const heading=el('div','','page-heading'),headingText=el('div');headingText.append(el('h1','Noise, made visible.'),el('p','See current sound conditions across your locations.'));heading.append(headingText,button('Manage locations',()=>location.hash='management','secondary'));container.append(heading);
 const demoNotice=el('div','Locations marked SIMULATED or SYNTHETIC use generated recordings. They are demonstrations, not physical sound measurements.','notice');demoNotice.hidden=!ctx.locations.some(l=>/simulat|synthetic/i.test(l.name));container.append(demoNotice);
 const stats=el('div','','stats');const counts={},notes={},statCards={};for(const[key,title,note,icon]of[['locations','Registered locations','Across your monitoring area','locations'],['reporting','Connected devices','Recent uploads or device messages','reporting'],['incidents','Active incidents','Includes recovery in progress','alert'],['stale','Devices needing attention','Connection or sound-reading readiness','stale']]){const card=el('div','',`stat stat-${key}`);statCards[key]=card;counts[key]=el('strong','—');notes[key]=el('span',note,'stat-note');card.append(el('div',title,'stat-label'),el('span','',`stat-icon icon icon-${icon}`),counts[key],notes[key]);stats.append(card);}container.append(stats);
 const contactFeedback=el('p','','muted small contact-feedback');contactFeedback.setAttribute('role','status');container.append(contactFeedback);
 const layout=el('div','','map-layout'),mapPanel=el('section','','panel map-panel'),mapHead=el('div','','panel-head'),mapTitle=el('div');mapTitle.append(el('h2','Your monitoring area'),el('p','Select a marker to explore a location.'));mapHead.append(mapTitle,button('Fit all',()=>fitMap()));mapPanel.append(mapHead);const mapWrap=el('div','','map-wrap'),mapNode=el('div','','geo-map');mapNode.id='geographic-map';mapNode.setAttribute('aria-label','Geographic map of noise monitoring locations');const mapError=el('div','Map tiles could not load. Your location list and markers remain available.','map-error');mapError.hidden=true;mapWrap.append(mapNode,mapError);mapPanel.append(mapWrap);const legend=el('div','','map-legend');for(const[label,tone]of[['Within threshold','good'],['Excessive / recovering','danger'],['Readings unavailable or stale','stale']]){const item=el('span');item.append(el('i','',`dot ${tone}`),document.createTextNode(label));legend.append(item)}mapPanel.append(legend);
 const listPanel=el('section','','panel location-panel'),listHead=el('div','','panel-head');const totalLabel=badge('0 locations');listHead.append(el('h2','Locations'),totalLabel);const list=el('div','','location-list');listPanel.tabIndex=-1;listPanel.setAttribute('aria-label','Monitoring locations');listPanel.append(listHead,list);layout.append(listPanel,mapPanel);container.append(layout);
 const activePanel=el('section','','panel overview-bottom'),activeHead=el('div','','panel-head'),activeTitle=el('div');activeTitle.append(el('h2','Incidents requiring attention'),el('p','An unresolved incident remains visible even when its device stops reporting.'));const historyButton=button('View history →',()=>location.hash='incidents');activeHead.append(activeTitle,historyButton);const activeList=el('div');activePanel.append(activeHead,activeList);container.insertBefore(activePanel,layout);
 let map=null,selected=null,destroyed=false,hasFit=false;const contacts=new Map(),markers=new Map();try{if(!window.L)throw new Error('Map library unavailable');map=L.map(mapNode,{scrollWheelZoom:false}).setView([20,0],2);const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'}).addTo(map);tiles.on('tileerror',()=>mapError.hidden=false);}catch{mapError.hidden=false;}
 function locations(){return ctx.locations.map(meta=>({...meta,...(state.locations.get(meta.id)||{streams:[],devices:[],noise_status:'unknown',data_status:'unknown'}),name:meta.name,latitude:meta.latitude,longitude:meta.longitude}));}
 function fitMap(){if(!map)return;const positions=locations().filter(validPoint).map(l=>[l.latitude,l.longitude]);if(positions.length){map.fitBounds(positions,{padding:[48,48],maxZoom:14});hasFit=true;}}
 function validPoint(l){return Number.isFinite(l.latitude)&&Number.isFinite(l.longitude)&&Math.abs(l.latitude)<=90&&Math.abs(l.longitude)<=180;}
 function health(l){return summarizeLocationConnection(ctx.devices,l,l,{contacts,now:ctx.serverNow(),staleSeconds:ctx.dataStaleSeconds()});}
 function readingFor(l){
  const assigned=new Map(ctx.devices.filter(d=>d.enabled&&d.location_id===l.id).map(d=>[d.id,d]));
  return latest({...l,streams:(l.streams||[]).filter(stream=>{
   const device=assigned.get(stream.device_id);
   return device&&device.current_assignment_id===stream.assignment_id&&stream.measurement_type===l.current_threshold?.threshold_type&&
    !(stream.measurement_type==='spl_z_leq'&&!device.calibration);
  })});
 }
 function locationBadges(l){
  const h=health(l),c=condition(l),box=el('span','','status-group');
  box.append(badge(h.label,h.total&&h.connected===h.total?'good':'stale'));
  if(c.incident||!h.needsCalibration||h.calibrationCount<h.total)box.append(statusBadges(l));
  if(h.needsCalibration)box.append(badge('Calibration required','stale'));
  return box;
 }
 function markerLabel(l){const h=health(l),c=condition(l);return `${l.name}: ${h.label}; ${h.needsCalibration?'Calibration required; ':''}${c.label}`;}
 function markerIcon(l){const c=condition(l),h=health(l);return L.divIcon({className:'',html:`<span class="map-pin ${h.needsCalibration&&!c.incident?'stale':c.tone} ${c.incident?'unresolved':''} ${selected===l.id?'selected':''}"></span>`,iconSize:[30,30],iconAnchor:[15,15]});}
 // Polling replaces rows and Leaflet icons; retain the user's place and control.
 function rememberFocus(){
  const element=document.activeElement;
  return {element,key:element?.dataset?.overviewFocus,markerId:[...markers].find(([,marker])=>marker.getElement()===element)?.[0],scrollTop:list.scrollTop,scrollLeft:list.scrollLeft};
 }
 function restoreFocus(saved){
  let target=saved.key?[...container.querySelectorAll('[data-overview-focus]')].find(node=>node.dataset.overviewFocus===saved.key):null;
  if(saved.markerId)target=markers.get(saved.markerId)?.getElement();
  // If the focused incident resolves or a location disappears, keep navigation nearby.
  if(!target&&saved.key)target=saved.key.startsWith('incident:')?historyButton:listPanel;
  if(target&&target!==document.activeElement)target.focus({preventScroll:true});
  list.scrollTop=saved.scrollTop;list.scrollLeft=saved.scrollLeft;
 }
 function selectLocation(id,pan=true){
  const saved=rememberFocus();selected=id;
  for(const row of list.children)row.classList.toggle('selected',row.dataset.location===id);
  for(const l of locations())markers.get(l.id)?.setIcon(markerIcon(l));
  const marker=markers.get(id);
  if(marker){marker.openPopup();if(pan)map.panTo(marker.getLatLng());}
  restoreFocus(saved);
  if(marker&&pan&&window.matchMedia?.('(max-width: 600px)').matches){
   mapPanel.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
  }
 }
 function popup(l){const node=el('div'),reading=readingFor(l),threshold=l.current_threshold,h=health(l);node.append(el('h3',l.name),locationBadges(l),el('p',formatLevel(reading?.measurement_value,reading?.measurement_type)),el('p',`Threshold: ${formatLevel(threshold?.threshold_value,threshold?.threshold_type)}`),el('p',`Last device contact: ${formatTime(h.lastContact,l.timezone)}`));if(reading)node.append(el('p',`Measured: ${formatTime(reading.measured_at,l.timezone)}`));if(h.needsCalibration)node.append(el('p','SPL readings need microphone calibration. Contact does not establish a calibrated sound level.'));const a=el('a','View location →');a.href=`#location/${l.id}`;a.dataset.overviewFocus=`popup:${l.id}`;a.setAttribute('aria-label',`View location details for ${l.name}`);node.append(a);return node;}
 function update(){
  if(destroyed)return;
  const saved=rememberFocus();
  const locs=locations(),active=[...state.incidents.values()].filter(i=>['active','recovering'].includes(i.status));
  counts.locations.textContent=ctx.locations.length;
  let connected=0,reporting=0,attention=0,calibration=0,disconnected=0,readingAttention=0;
  for(const d of ctx.devices.filter(d=>d.enabled)){
   const current=state.locations.get(d.location_id),meta=ctx.locations.find(l=>l.id===d.location_id);
   const connection=deviceConnection(d,current,{contact:contacts.get(d.id),now:ctx.serverNow(),staleSeconds:ctx.dataStaleSeconds()}),r=deviceReadingStatus(d,meta,current);
   if(connection.connected)connected++;else disconnected++;
   if(r.reporting&&!r.needsCalibration)reporting++;
   if(r.needsCalibration)calibration++;
   if(!r.needsCalibration&&(!r.reporting||r.attention))readingAttention++;
   if(!connection.connected||!r.reporting||r.attention||r.needsCalibration)attention++;
  }
  counts.reporting.textContent=connected;notes.reporting.textContent=`${reporting} with usable readings · contact checked every 2 s`;
  counts.stale.textContent=attention;notes.stale.textContent=attention?[calibration?`${calibration} need calibration`:'',disconnected?`${disconnected} without recent contact`:'',readingAttention?`${readingAttention} need usable readings`:''].filter(Boolean).join(' · '):'All enabled devices have recent usable readings';
  counts.incidents.textContent=active.length;
  statCards.incidents.classList.toggle('alert-stat',active.length>0);
  statCards.stale.classList.toggle('attention-stat',attention>0);
  totalLabel.textContent=`${locs.length} locations`;demoNotice.hidden=!locs.some(l=>/simulat|synthetic/i.test(l.name));
  const prioritizeIncidents=active.length>0;
  activePanel.classList.toggle('overview-priority',prioritizeIncidents);
  list.replaceChildren();
  for(const l of locs){
   const reading=readingFor(l),h=health(l),row=el('article','',`location-row ${selected===l.id?'selected':''}`);row.dataset.location=l.id;
   const top=el('div','','row-top'),choose=button(l.name,()=>selectLocation(l.id),'location-select');
   choose.dataset.selectLocation=l.id;choose.dataset.overviewFocus=`location:${l.id}`;
   choose.title=`Show ${l.name} on map`;choose.setAttribute('aria-label',choose.title);top.append(choose,locationBadges(l));
   const level=el('div',Number.isFinite(reading?.measurement_value)?reading.measurement_value.toFixed(1):'—','level');
   if(Number.isFinite(reading?.measurement_value))level.append(el('span',unit(reading.measurement_type),'unit'));
   const limit=el('div',`Threshold ${formatLevel(l.current_threshold?.threshold_value,l.current_threshold?.threshold_type)}`,'muted small'),bottom=el('div','','row-bottom'),link=el('a','Details →');
   link.href=`#location/${l.id}`;link.dataset.overviewFocus=`details:${l.id}`;link.setAttribute('aria-label',`View location details for ${l.name}`);
   bottom.append(el('span',reading?`Measured ${formatTime(reading.measured_at,l.timezone)}`:h.needsCalibration?'Awaiting calibrated reading':'No eligible readings yet'),link);
   row.append(top,level,limit,el('p',`Last device contact: ${formatTime(h.lastContact,l.timezone)}`,'muted small'));
   if(h.needsCalibration)row.append(el('p','SPL readings need microphone calibration. Recordings can arrive while calibrated readings remain unavailable.','muted small'));
   row.append(bottom);list.append(row);
   if(map&&validPoint(l)){
    let marker=markers.get(l.id);
    if(!marker){
     marker=L.marker([l.latitude,l.longitude],{icon:markerIcon(l),title:l.name,alt:markerLabel(l),keyboard:true}).addTo(map);
     marker.bindPopup(popup(l));marker.on('click',()=>selectLocation(l.id,false));markers.set(l.id,marker);
    }else{marker.setLatLng([l.latitude,l.longitude]);marker.setIcon(markerIcon(l));marker.setPopupContent(popup(l));}
    marker.options.title=l.name;marker.options.alt=markerLabel(l);
    const markerNode=marker.getElement();
    if(markerNode){markerNode.title=l.name;markerNode.setAttribute('aria-label',marker.options.alt);}
   }
  }
  if(!locs.length)list.append(empty('No locations registered','Add your first location in Management.'));
  if(map&&!hasFit&&locs.length)fitMap();
  activeList.replaceChildren();
  for(const i of active.sort((a,b)=>Date.parse(b.started_at)-Date.parse(a.started_at)).slice(0,5)){
   const row=el('div','','incident-preview'),a=el('div'),b=el('div'),name=ctx.locations.find(l=>l.id===i.location_id)?.name||i.location_snapshot?.name||short(i.location_id);
   a.append(el('strong',name),el('small',`Device ${deviceLabel(i,ctx.devices)}`));
   b.append(badge(i.status,i.status),el('small',`Peak ${formatLevel(i.peak_db,i.threshold_type||i.measurement_type)} · Threshold ${formatLevel(i.threshold_value,i.threshold_type||i.measurement_type)}`));
   const action=button('View incident',()=>location.hash=`incident/${i.id}`);action.dataset.overviewFocus=`incident:${i.id}`;action.setAttribute('aria-label',`View incident at ${name}`);
   row.append(a,b,action);activeList.append(row);
  }
  if(!active.length)activeList.append(empty('No unresolved noise incidents','New incidents will appear here when an eligible reading exceeds its location’s threshold.'));
  restoreFocus(saved);
 }

 update();const stopContacts=startDeviceContactMonitor({api:ctx.api,signal:ctx.signal,contacts,now:()=>ctx.serverNow(),onUpdate:update,onError:error=>{if(!destroyed)contactFeedback.textContent=error?'Contact check unavailable; retrying. Times shown are the last confirmed contact.':'';}});requestAnimationFrame(()=>{if(!destroyed){map?.invalidateSize();fitMap()}});return{update,destroy(){destroyed=true;stopContacts();map?.remove();}};
}

if(isLocalHost)void startLocal();else{$('startup').hidden=true;$('login').hidden=false;}
window.addEventListener('pageshow',event=>{if(event.persisted&&isLocalHost)void startLocal();});
