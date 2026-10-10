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
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.dataset={};this.className='';this._text='';this.listeners={};this.classList={toggle:()=>{}};}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(child=>child.textContent??String(child)).join(' ');}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this._text='';this.children=[];this.append(...children);}
 setAttribute(key,value){this.attributes[key]=String(value);}
 addEventListener(key,handler){this.listeners[key]=handler;}
 all(){return this.children.flatMap(child=>child instanceof Element?[child,...child.all()]:[]);}
 querySelector(){return null;}
}
async function fixture(t,{incident=false}={}){
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
 const map={setView(){return this;},fitBounds(){},invalidateSize(){},remove(){this.removed=true;}};
 const L={map:()=>map,tileLayer:()=>({addTo:()=>({on(){}})}),divIcon:options=>options,marker:(point,options)=>{const marker={options,node:new Element('marker'),addTo(){markers.push(this);return this;},bindPopup(value){this.popup=value;},on(){},setLatLng(){},setIcon(value){this.icon=value;},setPopupContent(value){this.popup=value;},getElement(){return this.node;}};return marker;}};
 const values={...ui,...health,ctx,state,location:{hash:'overview'},window:{L},L,requestAnimationFrame:fn=>fn()};
 const mount=new Function(...Object.keys(values),`${overviewSource};return mountOverview;`)(...Object.values(values));
 const root=new Element('main'),view=mount(root);t.after(()=>view.destroy());await settle();
 return {root,ctx,state,view,requests,markers,map,controller,setNow:value=>{now=value;},now:()=>now,setFailure:value=>{fail=value;},hang:()=>{resolvePending=true;},release:()=>{resolvePending?.({items:devices.map(d=>({id:d.id,last_contact_at:instant()})),total:devices.length});},
  stat:title=>root.all().find(node=>node.className.split(' ').includes('stat')&&node.children[0]?.textContent===title),
  row:id=>root.all().find(node=>node.dataset.location===id)};
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
