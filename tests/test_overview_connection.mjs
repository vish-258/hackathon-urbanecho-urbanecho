import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as ui from '../app/static/application/ui.mjs';
import * as health from '../app/static/application/device-health.mjs';

// Exercise the real Overview function without starting the application's auth
// and event stream. Its route dependencies are supplied just as in the app.
const source=await readFile(new URL('../app/static/application/app.mjs',import.meta.url),'utf8');
const overviewSource=source.slice(source.indexOf('function mountOverview(container){'),source.indexOf('\nif(isLocalHost)void startLocal();'));
const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
class Element {
 constructor(tag){
  this.tag=tag;this.children=[];this.attributes={};this.dataset={};this.className='';this._text='';this.listeners={};this.parentNode=null;this.scrollTop=0;this.scrollLeft=0;
  this.classList={contains:name=>this.className.split(' ').includes(name),toggle:(name,force)=>{
   const tokens=new Set(this.className.split(' ').filter(Boolean)),enabled=force??!tokens.has(name);
   if(enabled)tokens.add(name);else tokens.delete(name);this.className=[...tokens].join(' ');return enabled;
  }};
 }
 set textContent(value){this.replaceChildren();this._text=String(value);}
 get textContent(){return this._text+this.children.map(child=>child.textContent??String(child)).join(' ');}
 append(...children){for(const child of children){child.parentNode?.removeChild(child);this.children.push(child);child.parentNode=this;}}
 removeChild(child){
  if(child===globalThis.document?.activeElement||child.contains?.(globalThis.document?.activeElement))globalThis.document.activeElement=null;
  this.children=this.children.filter(node=>node!==child);child.parentNode=null;return child;
 }
 insertBefore(child,before){child.parentNode?.removeChild(child);this.children.splice(this.children.indexOf(before),0,child);child.parentNode=this;}
 replaceChildren(...children){for(const child of [...this.children])this.removeChild(child);this._text='';this.scrollTop=0;this.scrollLeft=0;this.append(...children);}
 setAttribute(key,value){this.attributes[key]=String(value);}
 addEventListener(key,handler){this.listeners[key]=handler;}
 all(){return this.children.flatMap(child=>child instanceof Element?[child,...child.all()]:[]);}
 contains(node){return !!node&&(node===this||this.all().includes(node));}
 querySelectorAll(selector){
  if(selector==='[data-overview-focus]')return this.all().filter(node=>node.dataset.overviewFocus);
  return [];
 }
 focus(options){globalThis.document.activeElement=this;this.focusOptions=options;}
 scrollIntoView(options){this.scrolledIntoView=options;}
}

async function fixture(t,{incident=false,mobile=false,reducedMotion=false}={}){
 t.mock.timers.enable({apis:['setTimeout']});
 const previous=globalThis.document;
 globalThis.document={createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text}),activeElement:null};
 t.after(()=>{globalThis.document=previous;});
 let now=Date.parse('2026-10-10T00:00:00Z');
 const instant=()=>new Date(now).toISOString();
 const devices=['digital','spl'].map(id=>({id,location_id:id,current_assignment_id:id,enabled:true,calibration:null,last_contact_at:instant()}));
 const locations=devices.map(d=>({id:d.id,name:d.id==='digital'?'Room1':'Room2',latitude:17,longitude:78,timezone:'Asia/Kolkata',current_threshold:{threshold_type:d.id==='digital'?'dbfs_rms':'spl_z_leq',threshold_value:d.id==='digital'?-30:60}}));
 const state={locations:new Map(locations.map(l=>[l.id,{...l,data_status:l.id==='digital'?'fresh':'stale',noise_status:l.id==='digital'?'normal':'unknown',unresolved_incident_ids:[],streams:[{id:l.id,device_id:l.id,assignment_id:l.id,measurement_type:l.current_threshold.threshold_type,measurement_value:l.id==='digital'?-42:null,measured_at:l.id==='digital'?instant():null,received_at:l.id==='digital'?instant():null,data_status:l.id==='digital'?'fresh':'stale'}]}])),incidents:new Map()};
 if(incident){state.locations.get('spl').unresolved_incident_ids=['incident'];state.locations.get('spl').noise_status='excessive';state.incidents.set('incident',{id:'incident',location_id:'spl',device_id:'spl',status:'active',measurement_type:'spl_z_leq',threshold_value:60,peak_db:80,started_at:instant()});}
 const controller=new AbortController(),requests=[],markers=[];let fail=false,resolvePending=null;
 const ctx={devices,locations,signal:controller.signal,serverNow:()=>now,dataStaleSeconds:()=>30,async api(path,options){requests.push({path,options});if(resolvePending)return new Promise(resolve=>{resolvePending=resolve;});if(fail)throw Error('network unavailable');return {items:devices.map(d=>({id:d.id,last_contact_at:instant()})),total:devices.length};}};
 let mapNode;const map={setView(){return this;},fitBounds(){},panTo(point){this.pannedTo=point;},invalidateSize(){},remove(){this.removed=true;}};
 const L={map:node=>{mapNode=node;return map;},tileLayer:()=>({addTo:()=>({on(){}})}),divIcon:options=>options,marker:(point,options)=>{
  const marker={options,node:new Element('marker'),listeners:{},point,addTo(){markers.push(this);mapNode.append(this.node);return this;},bindPopup(value){this.popup=value;},on(event,callback){this.listeners[event]=callback;},setLatLng(value){this.point=value;},getLatLng(){return this.point;},
   setIcon(value){this.icon=value;this.node.parentNode?.removeChild(this.node);this.node=new Element('marker');mapNode.append(this.node);},
   setPopupContent(value){if(this.popup.parentNode){this.popup.parentNode.removeChild(this.popup);mapNode.append(value);}this.popup=value;},getElement(){return this.node;},openPopup(){mapNode.append(this.popup);}};
  return marker;
 }};
 const values={...ui,...health,ctx,state,location:{hash:'overview'},window:{L,matchMedia:query=>({matches:query.includes('prefers-reduced-motion')?reducedMotion:mobile})},L,requestAnimationFrame:fn=>fn()};

 const mount=new Function(...Object.keys(values),`${overviewSource};return mountOverview;`)(...Object.values(values));
 const root=new Element('main'),view=mount(root);t.after(()=>view.destroy());await settle();
 return {root,ctx,state,view,requests,markers,map,controller,setNow:value=>{now=value;},now:()=>now,setFailure:value=>{fail=value;},hang:()=>{resolvePending=true;},release:()=>{resolvePending?.({items:devices.map(d=>({id:d.id,last_contact_at:instant()})),total:devices.length});},
  stat:title=>root.all().find(node=>node.className.split(' ').includes('stat')&&node.children[0]?.textContent===title),
  row:id=>root.all().find(node=>node.dataset.location===id),control:key=>root.all().find(node=>node.dataset.overviewFocus===key),section:className=>root.all().find(node=>node.classList.contains(className))};
}

test('Overview shows both contacts while Room2 remains explicitly uncalibrated',async t=>{
 const f=await fixture(t);
 assert.equal(f.stat('Connected devices').children[2].textContent,'2');
 assert.equal(f.stat('Devices needing attention').children[2].textContent,'1');
 assert.match(f.row('spl').textContent,/Connected Calibration required/);
 assert.match(f.row('spl').textContent,/Awaiting calibrated reading/);
 assert.doesNotMatch(f.row('spl').textContent,/Within threshold/);
 assert.equal(f.row('spl').all().find(node=>node.className==='level').textContent,'—');
 assert.match(f.markers[1].popup.textContent,/Connected Calibration required/);
 assert.match(f.markers[1].options.alt,/Connected; Calibration required/);
 assert.equal(f.state.locations.get('spl').data_status,'stale');
 assert.equal(f.ctx.locations[1].current_threshold.threshold_type,'spl_z_leq');
 assert.equal(f.requests.length,1);
});

test('Overview contacts advance without eligible events and navigation stops checks',async t=>{
 const f=await fixture(t),before=f.row('spl').textContent;
 f.setNow(f.now()+2000);t.mock.timers.tick(2000);await settle();
 assert.notEqual(f.row('spl').textContent,before);
 assert.equal(f.requests.length,2);assert.match(f.row('spl').textContent,/Connected Calibration required/);
 f.view.destroy();const rendered=f.root.textContent;
 assert.equal(f.requests.at(-1).options.signal.aborted,true);
 t.mock.timers.tick(10000);await settle();
 assert.equal(f.requests.length,2);assert.equal(f.root.textContent,rendered);assert.equal(f.map.removed,true);
});

test('failed contact checks age connections while preserving calibration and unresolved incidents',async t=>{
 const f=await fixture(t,{incident:true});
 const before=structuredClone(f.state);
 f.setFailure(true);f.setNow(f.now()+31000);t.mock.timers.tick(2000);await settle();
 assert.equal(f.stat('Connected devices').children[2].textContent,'0');
 assert.equal(f.stat('Devices needing attention').children[2].textContent,'2');
 assert.equal(f.stat('Active incidents').children[2].textContent,'1');
 assert.match(f.row('spl').textContent,/No recent contact/);
 assert.match(f.row('spl').textContent,/Excessive noise/);
 assert.match(f.row('spl').textContent,/Calibration required/);
 assert.match(f.root.textContent,/Contact check unavailable; retrying/);
 assert.match(f.markers[1].icon.html,/unresolved/);
 assert.deepEqual(f.state,before);
});

test('a late contact response cannot repaint a destroyed Overview',async t=>{
 const f=await fixture(t);f.hang();t.mock.timers.tick(2000);await settle();
 const request=f.requests.at(-1);f.view.destroy();const before=f.root.textContent;
 f.setNow(f.now()+3000);f.release();await settle();t.mock.timers.tick(10000);await settle();
 assert.equal(request.options.signal.aborted,true);
 assert.equal(f.requests.length,2);assert.equal(f.root.textContent,before);
});


test('Overview incident priority follows unresolved state without relocating its section',async t=>{
 const f=await fixture(t,{incident:true}),incidents=f.stat('Active incidents'),panel=f.section('overview-bottom'),layout=f.section('map-layout');
 assert.equal(incidents.children[2].textContent,'1');assert.equal(incidents.classList.contains('alert-stat'),true);
 assert.ok(f.root.children.indexOf(panel)<f.root.children.indexOf(layout));
 const action=f.control('incident:incident');action.focus();
 f.state.incidents.get('incident').status='recovering';f.view.update();
 assert.equal(document.activeElement,f.control('incident:incident'));
 assert.equal(incidents.classList.contains('alert-stat'),true);
 f.state.incidents.get('incident').status='resolved';f.view.update();
 assert.equal(incidents.children[2].textContent,'0');assert.equal(incidents.classList.contains('alert-stat'),false);
 assert.equal(document.activeElement.textContent,'View history →');
 assert.ok(f.root.children.indexOf(panel)<f.root.children.indexOf(layout),'incident section keeps its position after the focused incident resolves');
 document.activeElement=null;f.view.update();
 assert.ok(f.root.children.indexOf(panel)<f.root.children.indexOf(layout));
 f.control('details:digital').focus();f.state.incidents.get('incident').status='active';f.view.update();
 assert.equal(document.activeElement,f.control('details:digital'));
 assert.ok(f.root.children.indexOf(panel)<f.root.children.indexOf(layout),'new incident keeps the same section order');
 document.activeElement=null;f.view.update();
 assert.ok(f.root.children.indexOf(panel)<f.root.children.indexOf(layout));
});

test('Overview attention emphasis clears once enabled devices have usable readings',async t=>{
 const f=await fixture(t),attention=f.stat('Devices needing attention');
 assert.equal(attention.children[2].textContent,'1');assert.equal(attention.classList.contains('attention-stat'),true);
 // Disable the uncalibrated device: the remaining digital device is healthy.
 f.ctx.devices.find(device=>device.id==='spl').enabled=false;f.view.update();
 assert.equal(attention.children[2].textContent,'0');assert.equal(attention.classList.contains('attention-stat'),false);
 assert.equal(f.stat('Active incidents').classList.contains('alert-stat'),false);
});

test('contact polling preserves location control focus and list scroll in both directions',async t=>{
 const f=await fixture(t),list=f.section('location-list');
 for(const key of ['location:digital','details:spl']){
  const old=f.control(key);old.focus();list.scrollTop=113;list.scrollLeft=7;
  f.setNow(f.now()+2000);t.mock.timers.tick(2000);await settle();
  assert.notEqual(f.control(key),old,'polling replaces the row');
  assert.equal(document.activeElement,f.control(key));assert.deepEqual(document.activeElement.focusOptions,{preventScroll:true});
  assert.equal(list.scrollTop,113);assert.equal(list.scrollLeft,7);
 }
 const outside=new Element('button');outside.focus();f.view.update();
 assert.equal(document.activeElement,outside,'background updates must not steal focus');
});

test('Overview polling restores keyboard focus when map icons and popup links are replaced',async t=>{
 const f=await fixture(t),marker=f.markers[0],old=marker.node;
 old.focus();f.view.update();
 assert.notEqual(marker.node,old);assert.equal(document.activeElement,marker.node);assert.deepEqual(marker.node.focusOptions,{preventScroll:true});
 marker.openPopup();f.control('popup:digital').focus();f.view.update();
 assert.equal(document.activeElement,f.control('popup:digital'));assert.deepEqual(document.activeElement.focusOptions,{preventScroll:true});
});

test('a removed location control returns focus to its location region',async t=>{
 const f=await fixture(t);f.control('details:digital').focus();f.ctx.locations=f.ctx.locations.filter(location=>location.id!=='digital');f.view.update();
 assert.equal(document.activeElement,f.section('location-panel'));assert.equal(document.activeElement.attributes['aria-label'],'Monitoring locations');
});

for(const reducedMotion of [false,true])test(`mobile map selection scrolls only on user selection (${reducedMotion?'reduced':'full'} motion)`,async t=>{
 const f=await fixture(t,{mobile:true,reducedMotion}),mapPanel=f.section('map-panel'),layout=f.section('map-layout');
 let choose=f.control('location:digital');
 assert.equal(layout.children[0],f.section('location-panel'));
 assert.equal(choose.attributes['aria-label'],'Show Room1 on map');
 assert.equal(f.control('details:digital').attributes['aria-label'],'View location details for Room1');
 assert.match(f.row('digital').textContent,/Measured /);
 f.view.update();assert.equal(mapPanel.scrolledIntoView,undefined);
 choose=f.control('location:digital');choose.focus();choose.listeners.click();
 assert.deepEqual(mapPanel.scrolledIntoView,{behavior:reducedMotion?'auto':'smooth',block:'start'});
 assert.deepEqual(f.map.pannedTo,[17,78]);
 mapPanel.scrolledIntoView=undefined;f.view.update();assert.equal(mapPanel.scrolledIntoView,undefined);
 f.markers[0].listeners.click();assert.equal(mapPanel.scrolledIntoView,undefined,'map marker clicks do not scroll the page');
});

test('desktop location selection keeps the map and page in place',async t=>{
 const f=await fixture(t);f.control('location:digital').listeners.click();
 assert.deepEqual(f.map.pannedTo,[17,78]);assert.equal(f.section('map-panel').scrolledIntoView,undefined);
});
