import test from 'node:test';
import assert from 'node:assert/strict';
import {deviceConnection, deviceReadingStatus, summarizeLocationConnection, startDeviceContactMonitor} from '../app/static/application/device-health.mjs';

const now = Date.parse('2026-10-10T00:00:30Z');
const time = seconds => new Date(now + seconds * 1000).toISOString();
const device = {id:'board', location_id:'room', enabled:true, current_assignment_id:'assignment', calibration:null};
const meta = {id:'room', current_threshold:{threshold_type:'spl_z_leq'}};
const stream = (values = {}) => ({device_id:'board', assignment_id:'assignment', data_status:'stale', ...values});
const settle = async () => {for(let i=0;i<30;i++)await Promise.resolve();};

test('contact is connected independently of calibration and eligible measurements', () => {
  const current = {streams:[stream({diagnostic:'missing_value'})], data_status:'stale', noise_status:'unknown'};
  const before = structuredClone(current);
  const connected = deviceConnection({...device,last_contact_at:time(-1)}, current, {now});
  const reading = deviceReadingStatus(device, meta, current);
  assert.deepEqual(connected,{connected:true,label:'Connected',contact:time(-1),ageSeconds:1});
  assert.equal(reading.label,'Calibration required');
  assert.equal(reading.reporting,false);
  assert.equal(reading.latest,undefined);
  assert.deepEqual(current,before);
});

test('connection uses the precise freshness boundary and respects disabled devices', () => {
  const board = {...device,last_contact_at:time(-30)};
  assert.equal(deviceConnection(board,undefined,{now}).connected,true);
  assert.equal(deviceConnection(board,undefined,{now:now+1}).label,'No recent contact');
  assert.equal(deviceConnection({...board,enabled:false},undefined,{now}).label,'Disabled');
  assert.equal(deviceConnection({...board,enabled:false},undefined,{now}).connected,false);
});

test('future and invalid timestamps cannot establish contact or hide a valid timestamp', () => {
  assert.deepEqual(deviceConnection({...device,last_contact_at:time(60)},undefined,{now}),
    {connected:false,label:'Check contact time',contact:time(60),ageSeconds:null});
  assert.equal(deviceConnection({...device,last_contact_at:time(-2)},undefined,{now,contact:time(60)}).contact,time(-2));
  assert.equal(deviceConnection({...device,last_contact_at:'invalid'},undefined,{now,contact:'not a date'}).label,'No contact yet');
});

test('former assignment readings neither establish contact nor become the current usable reading', () => {
  const current = {streams:[stream({assignment_id:'previous',received_at:time(-1),data_status:'fresh'})]};
  assert.equal(deviceConnection(device,current,{now}).label,'No contact yet');
  assert.equal(deviceReadingStatus(device,{threshold_type:'dbfs_rms'},current).reporting,false);
  assert.equal(deviceReadingStatus(device,meta,current).latest,undefined);
  current.streams.push(stream({received_at:time(-4),data_status:'invalid'}));
  assert.equal(deviceConnection(device,current,{now}).contact,time(-4));
  assert.equal(deviceReadingStatus(device,{threshold_type:'dbfs_rms'},current).label,'Reporting · needs attention');
});

test('digital levels do not demand calibration and disabled devices do not claim readings', () => {
  const current = {streams:[stream({received_at:time(-1),data_status:'fresh'})]};
  assert.equal(deviceReadingStatus(device,{threshold_type:'dbfs_rms'},current).label,'Reporting');
  assert.equal(deviceReadingStatus(device,{threshold_type:'dbfs_rms'},current).needsCalibration,false);
  assert.equal(deviceReadingStatus({...device,calibration:{version:'measured'}},meta,current).label,'Reporting');
  assert.equal(deviceReadingStatus({...device,enabled:false},meta,current).label,'Disabled');
  assert.equal(deviceReadingStatus({...device,enabled:false},meta,current).reporting,false);
});

test('location totals separate connectivity, calibration and eligible readings without altering incidents', () => {
  const devices = [{...device,last_contact_at:time(-1)},
    {...device,id:'second',last_contact_at:time(-60),calibration:{version:'real'}},
    {...device,id:'disabled',enabled:false},
    {...device,id:'elsewhere',location_id:'other',last_contact_at:time(-1)}];
  const current = {unresolved_incident_ids:['incident'],noise_status:'excessive',data_status:'stale',streams:[]};
  const before = structuredClone(current);
  const summary = summarizeLocationConnection(devices,meta,current,{now});
  assert.deepEqual(summary,{connected:1,total:2,label:'Some devices disconnected',needsCalibration:true,
    calibrationCount:1,reporting:0,attention:0,lastContact:time(-1)});
  assert.deepEqual(current,before);
  assert.equal(summarizeLocationConnection([],meta,current,{now}).label,'No devices');
  assert.equal(summarizeLocationConnection([{...device,enabled:false}],meta,current,{now}).label,'Devices disabled');
  assert.equal(summarizeLocationConnection([device],meta,current,{now,contacts:new Map([['board',time(-1)]])}).label,'Connected');
});

test('contact monitor publishes complete pages and never regresses confirmed valid contact',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const contacts=new Map([['board',time(-1)]]),requests=[],errors=[];
  let updates=0;
  const stop=startDeviceContactMonitor({contacts,now:()=>now,onUpdate:()=>updates++,onError:error=>errors.push(error),
    api:async path=>{requests.push(path);return path.endsWith('offset=0')
      ? {items:[{id:'board',last_contact_at:time(-10)}],total:2}
      : {items:[{id:'second',last_contact_at:time(-2)}],total:2};}});
  t.after(stop);await settle();
  assert.equal(requests.length,2);assert.equal(updates,1);assert.deepEqual(errors,[null]);
  assert.equal(contacts.get('board'),time(-1));assert.equal(contacts.get('second'),time(-2));
  t.mock.timers.tick(1000);assert.equal(updates,2);
  assert.equal(requests.length,2);
});

test('a failed page retains confirmed contacts and a retry can recover from a future cached time',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const contacts=new Map([['board',time(60)]]),errors=[];
  let fail=true;
  const stop=startDeviceContactMonitor({contacts,now:()=>now,onError:error=>errors.push(error),api:async path=>{
    if(path.endsWith('offset=0'))return {items:[{id:'board',last_contact_at:time(-1)}],total:2};
    if(fail)throw Error('connection interrupted');
    return {items:[{id:'second',last_contact_at:time(-2)}],total:2};
  }});
  t.after(stop);await settle();
  assert.equal(contacts.get('board'),time(60));assert.match(errors[0].message,/interrupted/);
  fail=false;t.mock.timers.tick(2000);await settle();
  assert.equal(contacts.get('board'),time(-1));assert.equal(errors.at(-1),null);
});

test('hung requests never overlap or freeze contact aging, and stop aborts and ignores late results',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const contacts=new Map();let calls=0,updates=0,resolve,requestSignal;
  const stop=startDeviceContactMonitor({contacts,now:()=>now,onUpdate:()=>updates++,api:(_,options)=>{
    calls++;requestSignal=options.signal;return new Promise(done=>{resolve=done;});
  }});
  t.mock.timers.tick(10000);await settle();
  assert.equal(calls,1);assert.ok(updates>0);
  stop();assert.equal(requestSignal.aborted,true);const oldUpdates=updates;
  resolve({items:[{id:'board',last_contact_at:time(-1)}],total:1});await settle();
  t.mock.timers.tick(10000);await settle();
  assert.equal(contacts.size,0);assert.equal(calls,1);assert.equal(updates,oldUpdates);
});

test('an already closed route starts no checks and later route abort cancels active checks',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const closed=new AbortController();closed.abort();let calls=0;
  startDeviceContactMonitor({signal:closed.signal,api:async()=>{calls++;}});
  t.mock.timers.tick(10000);assert.equal(calls,0);
  const controller=new AbortController();let requestSignal;
  const stop=startDeviceContactMonitor({signal:controller.signal,api:(_,options)=>{calls++;requestSignal=options.signal;return new Promise(()=>{});}});
  t.after(stop);controller.abort();assert.equal(requestSignal.aborted,true);
  t.mock.timers.tick(10000);assert.equal(calls,1);
});
