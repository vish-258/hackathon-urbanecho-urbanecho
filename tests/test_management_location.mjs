import test from 'node:test';
import assert from 'node:assert/strict';
import {mountManagement} from '../app/static/application/management.mjs';
import {formatTime} from '../app/static/application/ui.mjs';
import {ageData} from '../app/static/state.mjs';

class Element {
 constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.attributes={};this.className='';this.value='';this._text='';}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(x=>x.textContent??String(x)).join(' ');}
 append(...children){this.children.push(...children);}
 prepend(...children){this.children.unshift(...children);}
 select(){this.selected=true;}
 replaceChildren(...children){this._text='';this.children=[];this.append(...children);}
 setAttribute(key,value){this.attributes[key]=String(value);}
 addEventListener(event,handler){(this.listeners[event]||=[]).push(handler);}
 all(){return this.children.flatMap(x=>x instanceof Element?[x,...x.all()]:[]);}
 querySelector(selector){return this.all().find(x=>selector==='[type=submit]'?x.type==='submit':selector.startsWith('.')?x.className.split(' ').includes(selector.slice(1)):x.tag===selector);}
 async click(){if(this.disabled)return;for(const handler of this.listeners.click||[])await handler({});}
}
const position={latitude:12.1234567,longitude:77.7654321,accuracy:25,timestamp:Date.now()};

test('Management keeps section controls mounted and exposes the selected section',async t=>{
 const f=await setup(t,async()=>position);
 const locations=f.button('Locations'),devices=f.button('Devices');
 assert.equal(locations.attributes['aria-pressed'],'true');
 assert.equal(devices.attributes['aria-pressed'],'false');
 await devices.click();
 assert.equal(f.button('Devices'),devices,'switching sections retains the focused button');
 assert.equal(f.button('Locations'),locations);
 assert.equal(devices.attributes['aria-pressed'],'true');
 assert.equal(locations.attributes['aria-pressed'],'false');
 await locations.click();
 assert.equal(f.button('Locations'),locations);
 assert.equal(locations.attributes['aria-pressed'],'true');
});

async function setup(t,locate){
 const previous=globalThis.document;
 globalThis.document={createElement:tag=>new Element(tag)};
 t.after(()=>{globalThis.document=previous;});
 const controller=new AbortController(),requests=[],contactRequests=[],notices=[],listeners=new Set();
 let contactHandler,serverNow=Date.parse('2026-10-10T01:02:10Z');
 const location={id:'bench',name:'Bench',latitude:0,longitude:0,timezone:'Asia/Kolkata',configuration_version:'saved-version'};
 const ctx={locations:[location],devices:['a','b'].map(id=>({id,external_id:`ESP-${id}`,location_id:'bench',current_assignment_id:`assignment-${id}`,enabled:true,microphone_model:'INMP441'})),state:{locations:new Map(),incidents:new Map()},signal:controller.signal,
  serverNow:()=>serverNow,dataStaleSeconds:()=>30,
  onLive(listener){listeners.add(listener);return()=>listeners.delete(listener);},
  async api(path,options){if(path.startsWith('/devices?')){contactRequests.push({path,...options});return contactHandler?contactHandler(path,options):{items:ctx.devices.map(d=>({id:d.id,last_contact_at:d.last_contact_at})),total:ctx.devices.length};}requests.push({path,...options});Object.assign(location,JSON.parse(options.body));},async refresh(){},notify(message){notices.push(message);}};
 const root=new Element('main');
 const cleanup=await mountManagement(root,ctx,{locate});
 t.after(cleanup);
 const button=label=>root.all().find(x=>x.tag==='button'&&x.textContent===label);
 const field=label=>root.all().find(x=>x.tag==='label'&&x.children[0].textContent===label)?.children[1];
 await button('Edit').click();
 return {root,controller,requests,contactRequests,setContactHandler:handler=>{contactHandler=handler;},setServerNow:value=>{serverNow=value;},notices,button,field,cleanup,ctx,listeners,emit:changes=>{for(const listener of listeners)listener(changes);}};
}

test('automatic lookup fills both coordinates, shows accuracy and saves only on explicit form submission',async t=>{
 const f=await setup(t,async()=>position);
 assert.match(f.root.textContent,/2 assigned devices/);
 await f.button('Use my current location').click();
 assert.equal(f.field('Latitude').value,String(position.latitude));
 assert.equal(f.field('Longitude').value,String(position.longitude));
 assert.match(f.root.textContent,/Estimated accuracy: 25 metres/);
 assert.match(f.root.textContent,/Nothing has been saved yet/);
 assert.equal(f.requests.length,0);
 await f.root.querySelector('form').onsubmit({preventDefault(){}});
 assert.equal(f.requests.length,1);
 assert.equal(f.requests[0].method,'PATCH');
 assert.equal(f.requests[0].path,'/locations/bench');
 assert.deepEqual(JSON.parse(f.requests[0].body),{name:'Bench',latitude:position.latitude,longitude:position.longitude,timezone:'Asia/Kolkata',expected_version:'saved-version'});
});

test('permission failure preserves manually entered coordinates and allows retry',async t=>{
 let calls=0;
 const f=await setup(t,async()=>{if(!calls++)throw new Error('Location access was denied.');return position;});
 f.field('Latitude').value='11';f.field('Longitude').value='76';
 await f.button('Use my current location').click();
 assert.equal(f.field('Latitude').value,'11');assert.equal(f.field('Longitude').value,'76');
 assert.match(f.root.textContent,/Location access was denied/);
 assert.match(f.root.textContent,/saved location has not changed/);
 assert.equal(f.requests.length,0);
 assert.equal(f.button('Save location').disabled,false);
 await f.button('Use my current location').click();
 assert.equal(f.field('Latitude').value,String(position.latitude));
});

test('tab changes cancel lookup and a late fix cannot overwrite a different form',async t=>{
 let resolve,signal;
 const f=await setup(t,options=>{signal=options.signal;return new Promise(done=>{resolve=done;});});
 const pending=f.button('Use my current location').click();
 assert.equal(f.button('Save location').disabled,true);
 await f.root.querySelector('form').onsubmit({preventDefault(){}});
 assert.equal(f.requests.length,0);
 await f.button('Devices').click();
 assert.equal(signal.aborted,true);
 await f.button('Locations').click();
 f.field('Latitude').value='22';
 resolve(position);await pending;
 assert.equal(f.field('Latitude').value,'22');assert.equal(f.requests.length,0);
});

test('leaving Management aborts a pending fix and suppresses its result',async t=>{
 let resolve,signal;
 const f=await setup(t,options=>{signal=options.signal;return new Promise(done=>{resolve=done;});});
 const pending=f.button('Use my current location').click();
 f.controller.abort();assert.equal(signal.aborted,true);
 resolve(position);await pending;
 assert.equal(f.field('Latitude').value,0);assert.equal(f.requests.length,0);
});

test('broad location estimates are identified for review without silently saving them',async t=>{
 const f=await setup(t,async()=>({...position,accuracy:3500}));
 await f.button('Use my current location').click();
 assert.match(f.root.textContent,/3500 metres/);assert.match(f.root.textContent,/broad estimate/);
 assert.equal(f.requests.length,0);
});

test('manual coordinate changes clear the browser accuracy claim',async t=>{
 const f=await setup(t,async()=>position);
 await f.button('Use my current location').click();
 const latitude=f.field('Latitude');latitude.value='22';
 for(const handler of latitude.listeners.input)handler({});
 assert.doesNotMatch(f.root.textContent,/Estimated accuracy: 25/);
 assert.match(f.root.textContent,/Coordinates changed manually/);
 assert.equal(f.requests.length,0);
});

function deviceRow(f,id){return f.root.all().find(node=>node.tag==='tr'&&node.children[0]?.textContent.startsWith(`ESP-${id}`));}
function liveLocation(f,streams){
 const location={id:'bench',streams,devices:f.ctx.devices.map(d=>({id:d.id,enabled:d.enabled,assignment_id:d.current_assignment_id}))};
 f.ctx.state.locations.set('bench',location);return location;
}
function liveReading(id,receivedAt,status='fresh'){
 return {id:`stream-${id}`,device_id:id,assignment_id:`assignment-${id}`,received_at:receivedAt,measured_at:receivedAt,data_status:status,noise_status:'normal'};
}

test('device rows receive live per-device status and timestamps without resetting an unsaved mapping',async t=>{
 const f=await setup(t,async()=>position);
 await f.button('Devices').click();await f.button('Edit mapping').click();
 const form=f.root.querySelector('form'),choice=f.field('Assigned location');choice.value='unsaved-choice';
 assert.match(deviceRow(f,'a').textContent,/No contact yet/);
 const receivedAt='2026-10-10T01:02:03Z',stream=liveReading('a',receivedAt);
 liveLocation(f,[stream]);f.emit();
 assert.match(deviceRow(f,'a').textContent,/Reporting/);
 assert.ok(deviceRow(f,'a').textContent.includes(`Last usable reading received: ${formatTime(receivedAt)}`));
 assert.match(deviceRow(f,'b').textContent,/No contact yet/);
 stream.data_status='invalid';stream.diagnostic='quality_clipped';f.emit();
 assert.match(deviceRow(f,'a').textContent,/Reporting · needs attention/);
 assert.match(deviceRow(f,'b').textContent,/No contact yet/);
 assert.equal(f.root.querySelector('form'),form);assert.equal(choice.value,'unsaved-choice');
 assert.equal(f.requests.length,0);
});

test('the existing freshness clock changes device status to stale and a new reading restores it',async t=>{
 const f=await setup(t,async()=>position);
 const receivedAt='2026-10-10T01:02:03Z',stream=liveReading('a',receivedAt);
 const location=liveLocation(f,[stream,liveReading('b','2026-10-10T01:02:25Z')]);
 await f.button('Devices').click();
 ageData(f.ctx.state,Date.parse(receivedAt)+31000,30);f.emit();
 assert.match(deviceRow(f,'a').textContent,/No recent usable data/);
 assert.match(deviceRow(f,'b').textContent,/Reporting/);
 location.streams[0]=liveReading('a','2026-10-10T01:02:35Z');f.emit();
 assert.match(deviceRow(f,'a').textContent,/Reporting/);
 assert.ok(deviceRow(f,'a').textContent.includes(formatTime('2026-10-10T01:02:35Z')));
 assert.equal(f.requests.length,0);
});

test('last usable reading stays assignment-specific and never substitutes a contact timestamp',async t=>{
 const f=await setup(t,async()=>position);
 f.ctx.devices[0].last_contact_at='2026-10-10T01:02:03Z';
 const retired={...liveReading('a','2026-10-10T01:02:04Z'),assignment_id:'retired-assignment'};
 liveLocation(f,[retired,liveReading('b','2026-10-10T01:02:05Z')]);
 await f.button('Devices').click();
 assert.match(deviceRow(f,'a').textContent,/No recent usable data/);
 assert.match(deviceRow(f,'a').textContent,/Last usable reading received: Not received/);
 f.ctx.devices[1].enabled=false;f.emit();
 assert.match(deviceRow(f,'b').textContent,/Disabled/);
});

test('an observed stream without a usable recording does not invent a connection or usable timestamp',async t=>{
 const f=await setup(t,async()=>position);
 await f.button('Devices').click();
 assert.match(deviceRow(f,'a').textContent,/No contact yet/);
 liveLocation(f,[{...liveReading('a',null,'stale'),diagnostic:'quality_clipped'}]);f.emit();
 assert.match(deviceRow(f,'a').textContent,/No recent usable data/);
 assert.match(deviceRow(f,'a').textContent,/Last usable reading received: Not received/);
 assert.match(deviceRow(f,'a').textContent,/No contact yet/);
});

test('configuration updates refresh displayed device locations without replacing an unsaved form',async t=>{
 const f=await setup(t,async()=>position);
 await f.button('Devices').click();await f.button('Edit mapping').click();
 const form=f.root.querySelector('form'),choice=f.field('Assigned location');choice.value='unsaved-choice';
 f.ctx.locations[0].name='Room1';
 f.ctx.locations.push({id:'second-room',name:'Room2',latitude:17,longitude:78,timezone:'Asia/Kolkata'});
 f.ctx.devices[1].location_id='second-room';
 f.emit({metadata:true});
 assert.match(deviceRow(f,'a').textContent,/Room1/);
 assert.match(deviceRow(f,'b').textContent,/Room2/);
 assert.match(deviceRow(f,'b').textContent,/17\.00000, 78\.00000/);
 assert.equal(f.root.querySelector('form'),form);assert.equal(choice.value,'unsaved-choice');
 assert.equal(f.requests.length,0);
});

test('live batches leave an automatic location lookup in progress and cleanup removes subscriptions',async t=>{
 let resolve,signal;
 const f=await setup(t,options=>{signal=options.signal;return new Promise(done=>{resolve=done;});});
 const form=f.root.querySelector('form'),pending=f.button('Use my current location').click();
 f.emit();assert.equal(signal.aborted,false);assert.equal(f.root.querySelector('form'),form);
 assert.equal(f.button('Save location').disabled,true);assert.equal(f.listeners.size,1);
 resolve(position);await pending;
 assert.equal(f.field('Latitude').value,String(position.latitude));
 await f.button('Devices').click();
 const before=f.root.textContent;f.controller.abort();
 assert.equal(f.listeners.size,0);
 liveLocation(f,[liveReading('a','2026-10-10T01:02:03Z')]);f.emit();
 assert.equal(f.root.textContent,before);f.cleanup();assert.equal(f.listeners.size,0);
});

const settle=async()=>{for(let n=0;n<10;n++)await Promise.resolve();};

test('contact polling advances without eligible readings and preserves device configuration and unsaved inputs',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const f=await setup(t,async()=>position);
 let contact='2026-10-10T01:02:03Z';
 f.setContactHandler(()=>({items:[{id:'a',last_contact_at:contact,location_id:'unrelated-location',enabled:false}],total:1}));
 assert.equal(f.contactRequests.length,0);
 await f.button('Devices').click();await settle();
 await f.button('Edit mapping').click();await settle();
 const form=f.root.querySelector('form'),choice=f.field('Assigned location');choice.value='unsaved-choice';
 assert.ok(deviceRow(f,'a').textContent.includes(`Last device contact: ${formatTime(contact)}`));
 assert.match(deviceRow(f,'a').textContent,/Last usable reading received: Not received/);
 assert.match(deviceRow(f,'a').textContent,/No recent usable data/);
 contact='2026-10-10T01:02:08Z';t.mock.timers.tick(2000);await settle();
 assert.ok(deviceRow(f,'a').textContent.includes(`Last device contact: ${formatTime(contact)}`));
 assert.equal(f.root.querySelector('form'),form);assert.equal(choice.value,'unsaved-choice');
 assert.equal(f.ctx.devices[0].location_id,'bench');assert.equal(f.ctx.devices[0].enabled,true);
 assert.equal(f.ctx.devices[0].last_contact_at,undefined);
 assert.equal(f.contactRequests.length,3);assert.equal(f.requests.length,0);
});

test('contact polling reads every device page before displaying the confirmed contacts',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const f=await setup(t,async()=>position),contact='2026-10-10T01:02:03Z';
 f.setContactHandler(path=>({items:[{id:path.includes('offset=0')?'a':'b',last_contact_at:contact}],total:2}));
 await f.button('Devices').click();await settle();
 assert.equal(f.contactRequests.length,2);
 assert.ok(f.contactRequests[1].path.endsWith('offset=1'));
 for(const id of ['a','b'])assert.ok(deviceRow(f,id).textContent.includes(`Last device contact: ${formatTime(contact)}`));
});

test('slow contact checks never overlap and leaving Devices aborts and stops polling',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const f=await setup(t,async()=>position);let resolve;
 f.setContactHandler(()=>new Promise(done=>{resolve=done;}));
 await f.button('Devices').click();
 t.mock.timers.tick(20000);await settle();assert.equal(f.contactRequests.length,1);
 const signal=f.contactRequests[0].signal;
 await f.button('Locations').click();assert.equal(signal.aborted,true);
 const before=f.root.textContent;
 resolve({items:[{id:'a',last_contact_at:'2026-10-10T01:02:03Z'}],total:1});await settle();
 t.mock.timers.tick(20000);await settle();
 assert.equal(f.contactRequests.length,1);assert.equal(f.root.textContent,before);
});

test('failed contact checks preserve confirmed times, retry, and stop when the route closes',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const f=await setup(t,async()=>position);let calls=0;
 const old='2026-10-10T01:02:03Z',latest='2026-10-10T01:02:13Z';
 f.setContactHandler(()=>{if(++calls===2)throw new Error('network unavailable');return {items:[{id:'a',last_contact_at:calls===1?old:latest}],total:1};});
 await f.button('Devices').click();await settle();
 t.mock.timers.tick(2000);await settle();
 assert.match(f.root.textContent,/Contact check unavailable; retrying/);
 assert.ok(deviceRow(f,'a').textContent.includes(`Last device contact: ${formatTime(old)}`));
 t.mock.timers.tick(2000);await settle();
 assert.doesNotMatch(f.root.textContent,/Contact check unavailable/);
 assert.ok(deviceRow(f,'a').textContent.includes(`Last device contact: ${formatTime(latest)}`));
 f.controller.abort();t.mock.timers.tick(20000);await settle();
 assert.equal(calls,3);assert.equal(f.contactRequests.at(-1).signal.aborted,true);
});

test('a form change cancels an old check and ignores its late response',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const f=await setup(t,async()=>position);let resolve,calls=0;
 const current='2026-10-10T01:02:03Z',late='2026-10-10T01:02:50Z';
 f.setContactHandler(()=>++calls===1?new Promise(done=>{resolve=done;}):{items:[{id:'a',last_contact_at:current}],total:1});
 await f.button('Devices').click();const oldSignal=f.contactRequests[0].signal;
 await f.button('Edit mapping').click();await settle();assert.equal(oldSignal.aborted,true);
 resolve({items:[{id:'a',last_contact_at:late}],total:1});await settle();
 assert.ok(deviceRow(f,'a').textContent.includes(`Last device contact: ${formatTime(current)}`));
 assert.ok(!deviceRow(f,'a').textContent.includes(formatTime(late)));
 f.cleanup();t.mock.timers.tick(20000);await settle();assert.equal(calls,2);
});

test('an uncalibrated SPL device is connected independently of its missing usable readings',async t=>{
 const f=await setup(t,async()=>position),contact='2026-10-10T01:02:09Z';
 f.ctx.locations[0].current_threshold={threshold_type:'spl_z_leq',threshold_value:60};
 f.ctx.devices[0].calibration=null;f.ctx.devices[0].last_contact_at=contact;
 liveLocation(f,[{...liveReading('a',null,'stale'),diagnostic:'missing_value'}]);
 await f.button('Devices').click();await settle();
 const text=deviceRow(f,'a').textContent;
 assert.match(text,/Connection Connected/);assert.match(text,/Readings Calibration required/);
 assert.match(text,/SPL readings need microphone calibration/);
 assert.ok(text.includes(`Last device contact: ${formatTime(contact)} (1 s ago)`));
 assert.doesNotMatch(text,/Last usable reading received: Not received/);
 assert.equal(f.ctx.locations[0].current_threshold.threshold_type,'spl_z_leq');
 assert.equal(f.requests.length,0);
 f.ctx.locations[0].current_threshold={threshold_type:'dbfs_rms',threshold_value:-30};f.emit({metadata:true});
 assert.doesNotMatch(deviceRow(f,'a').textContent,/Calibration required/);
 assert.match(deviceRow(f,'a').textContent,/Connection Connected/);
});

test('connection freshness uses the server-adjusted clock and configured stale window',async t=>{
 t.mock.method(Date,'now',()=>Date.parse('2026-10-09T01:02:10Z'));
 const f=await setup(t,async()=>position),contact='2026-10-10T01:02:09Z';
 f.ctx.devices[0].last_contact_at=contact;f.ctx.dataStaleSeconds=()=>10;
 await f.button('Devices').click();await settle();
 assert.match(deviceRow(f,'a').textContent,/Connection Connected/);
 f.setServerNow(Date.parse(contact)+10000);f.emit();
 assert.match(deviceRow(f,'a').textContent,/Connection Connected/);
 f.setServerNow(Date.parse(contact)+10001);f.emit();
 assert.match(deviceRow(f,'a').textContent,/Connection No recent contact/);
 f.ctx.devices[0].enabled=false;f.emit();
 assert.match(deviceRow(f,'a').textContent,/Connection Disabled/);
 assert.doesNotMatch(deviceRow(f,'a').textContent,/Connection Connected/);
});

test('future contact times never imply Connected and a subsequent valid contact recovers',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const f=await setup(t,async()=>position);let contact='2026-10-10T02:00:00Z';
 f.setContactHandler(()=>({items:[{id:'a',last_contact_at:contact}],total:1}));
 await f.button('Devices').click();await settle();
 assert.match(deviceRow(f,'a').textContent,/Connection Check contact time/);
 assert.doesNotMatch(deviceRow(f,'a').textContent,/Connection Connected/);
 contact='2026-10-10T01:02:09Z';t.mock.timers.tick(2000);await settle();
 assert.match(deviceRow(f,'a').textContent,/Connection Connected/);
 assert.ok(deviceRow(f,'a').textContent.includes(formatTime(contact)));
});

test('the connection ages out during failed polls without any live events',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const f=await setup(t,async()=>position),contact='2026-10-10T01:02:09Z';let calls=0;
 f.setContactHandler(()=>{if(++calls>1)throw new Error('server unavailable');return {items:[{id:'a',last_contact_at:contact}],total:1};});
 await f.button('Devices').click();await settle();
 assert.match(deviceRow(f,'a').textContent,/Connection Connected/);
 f.setServerNow(Date.parse(contact)+31000);t.mock.timers.tick(2000);await settle();
 assert.match(deviceRow(f,'a').textContent,/Connection No recent contact/);
 assert.match(f.root.textContent,/Contact check unavailable; retrying/);
 assert.equal(calls,2);assert.ok(deviceRow(f,'a').textContent.includes(formatTime(contact)));
});

test('a hung poll cannot freeze Connected and the status clock stops on tab exit',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const f=await setup(t,async()=>position),contact='2026-10-10T01:02:09Z';let resolve;
 f.ctx.devices[0].last_contact_at=contact;
 f.setContactHandler(()=>new Promise(done=>{resolve=done;}));
 await f.button('Devices').click();
 assert.match(deviceRow(f,'a').textContent,/Connection Connected/);
 f.setServerNow(Date.parse(contact)+31000);t.mock.timers.tick(1000);
 assert.match(deviceRow(f,'a').textContent,/Connection No recent contact/);
 assert.equal(f.contactRequests.length,1);
 await f.button('Locations').click();const before=f.root.textContent;
 resolve({items:[{id:'a',last_contact_at:'2026-10-10T01:03:00Z'}],total:1});await settle();
 t.mock.timers.tick(20000);await settle();
 assert.equal(f.root.textContent,before);assert.equal(f.contactRequests.length,1);
});

test('threshold guidance explains negative digital levels without altering the configured rule',async t=>{
 const f=await setup(t,async()=>position);
 const saved={threshold_type:'dbfs_rms',threshold_value:-30,interval_seconds:1,recovery_count:3,revision:7};
 const requests=[];
 f.ctx.api=async(path,options)=>{requests.push({path,...options});return {latest:saved,current:saved,latest_revision:7};};
 await f.button('Thresholds').click();await settle();
 assert.match(f.root.textContent,/−20 dBFS is louder than −30 dBFS; −40 dBFS is quieter/);
 assert.match(f.root.textContent,/above -30\.00 dBFS to open an incident on Overview/);
 assert.match(f.root.textContent,/exactly equal to the threshold does not open an incident/);
 assert.match(f.root.textContent,/3 consecutive usable readings at or below the threshold/);
 assert.match(f.root.textContent,/Clipped, silent or invalid recordings do not open incidents or count toward recovery/);
 assert.equal(f.field('Threshold').value,-30);assert.equal(f.field('Threshold').min,'-200');assert.equal(f.field('Threshold').max,'0');
 assert.equal(f.field('Recording duration (seconds)').value,1);
 assert.match(f.root.textContent,/For a 1-second recording every 10 seconds, keep this at 1/);
 assert.match(f.root.textContent,/Capture spacing is configured on the device; this form does not change it/);
 assert.doesNotMatch(f.root.textContent,/Reading interval \(seconds\)/);
 assert.equal(requests.length,1);assert.equal(requests[0].method,undefined);
 assert.deepEqual(saved,{threshold_type:'dbfs_rms',threshold_value:-30,interval_seconds:1,recovery_count:3,revision:7});
});

test('threshold guidance follows selected units and draft values without saving them',async t=>{
 const f=await setup(t,async()=>position);
 f.ctx.api=async()=>({latest:{threshold_type:'dbfs_rms',threshold_value:-30,interval_seconds:1,recovery_count:3},current:{threshold_type:'dbfs_rms',threshold_value:-30},latest_revision:2});
 await f.button('Thresholds').click();await settle();
 const method=f.field('Measurement unit'),level=f.field('Threshold');
 method.value='spl_z_leq';method.onchange();
 assert.match(f.root.textContent,/requires microphone calibration/);
 assert.match(f.root.textContent,/60 dB SPL is not the same as −30 dBFS/);
 assert.equal(level.value,-30);assert.equal(level.min,'-100');assert.equal(level.max,'200');
 level.value='60';level.oninput();
 assert.match(f.root.textContent,/above 60\.00 dB SPL \(Z\) to open an incident on Overview/);
 const recovery=f.field('Normal readings to recover');recovery.value='5';recovery.oninput();
 assert.match(f.root.textContent,/5 consecutive usable readings/);
 level.value='';level.oninput();
 assert.match(f.root.textContent,/Enter the level above which/);
 assert.equal(f.requests.length,0);
});

test('location save confirms inline after render and never requests a notification',async t=>{
 const f=await setup(t,async()=>position);
 const before=f.root.querySelector('form');
 await before.onsubmit({preventDefault(){}});await settle();
 const after=f.root.querySelector('form');
 assert.notEqual(before,after);
 assert.match(after.querySelector('.feedback').textContent,/Location updated\. History preserved/);
 assert.deepEqual(f.notices,[]);
});

test('threshold save confirmation survives the asynchronous form reload without notifications',async t=>{
 const f=await setup(t,async()=>position);
 let revision=1;
 f.ctx.api=async(path,options)=>{
  if(options?.method==='PATCH'){revision++;return {};}
  return {latest:{threshold_type:'dbfs_rms',threshold_value:-30,interval_seconds:1,recovery_count:3},current:{threshold_type:'dbfs_rms',threshold_value:-30},latest_revision:revision};
 };
 await f.button('Thresholds').click();await settle();
 await f.root.querySelector('form').onsubmit({preventDefault(){}});await settle();
 assert.match(f.root.querySelector('form').querySelector('.feedback').textContent,/Threshold saved as a new version/);
 assert.match(f.root.textContent,/Latest saved revision: 2/);
 assert.deepEqual(f.notices,[]);
});

test('registration refresh warnings and credential copy outcomes stay in the credential box',async t=>{
 const f=await setup(t,async()=>position);
 const descriptor=Object.getOwnPropertyDescriptor(globalThis,'navigator');
 let rejectCopy=false,copied;
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{async writeText(value){if(rejectCopy)throw Error('denied');copied=value;}}}});
 t.after(()=>{if(descriptor)Object.defineProperty(globalThis,'navigator',descriptor);else delete globalThis.navigator;});
 f.ctx.api=async path=>path.startsWith('/devices?')?{items:[],total:0}:{external_id:'ESP-test',token:'test-provisioned-token'};
 f.ctx.refresh=async()=>{throw Error('refresh unavailable');};
 await f.button('Devices').click();await settle();
 await f.root.querySelector('form').onsubmit({preventDefault(){}});await settle();
 const provision=f.root.querySelector('.provisioning');
 assert.ok(provision);assert.match(provision.textContent,/Device registered\. Reopen Management to refresh the list/);
 await f.button('Copy token').click();
 assert.equal(copied,'test-provisioned-token');assert.match(provision.textContent,/Device credential copied/);
 rejectCopy=true;await f.button('Copy token').click();
 assert.match(provision.textContent,/Select and copy the device credential/);
 assert.deepEqual(f.notices,[]);
});
