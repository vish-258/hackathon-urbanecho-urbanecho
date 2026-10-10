import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source=await readFile(new URL('../app/static/application/app.mjs',import.meta.url),'utf8');
const startup=source.slice(source.indexOf('async function startRemote(){'),source.indexOf("$('retry-local').onclick="));
function fixture(fetch){
 const nodes=new Map(),connections=[],errors=[],runs=[];
 const $=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,replaceChildren(){}});return nodes.get(id);};
 const values={$: $,fetch,AbortController,initialState:()=>({cursor:null}),connection:(...x)=>connections.push(x),startupError:message=>{errors.push(message);$('startup').hidden=false;$('login').hidden=true;},connectLoop:(signal,run)=>runs.push({signal,run})};
 const build=new Function(...Object.keys(values),`let publicAccess=false,publicAvailable=false,controller=null,generation=0;const state={};function disconnect(){generation++;controller?.abort();publicAccess=false;} ${startup};return {startRemote,disconnect,get publicAccess(){return publicAccess},get publicAvailable(){return publicAvailable}};`);
 return {...build(...Object.values(values)), nodes,$,connections,errors,runs};
}

test('remote public entry connects immediately without displaying sign-in or sending credentials',async()=>{
 let request;
 const f=fixture(async(path,options)=>{request={path,options};return{ok:true,json:async()=>({access:'public',read_only:true})};});
 await f.startRemote();
 assert.equal(request.path,'/app/access');assert.equal(request.options.headers,undefined);
 assert.equal(f.$('login').hidden,true);assert.equal(f.$('back-to-demo').hidden,false);
 assert.equal(f.runs.length,1);assert.equal(f.runs[0].signal.aborted,false);
});

test('private deployment still requires deliberate administrator sign-in',async()=>{
 const f=fixture(async()=>({ok:true,json:async()=>({access:'admin',read_only:false})}));
 await f.startRemote();assert.equal(f.$('login').hidden,false);assert.equal(f.$('startup').hidden,true);assert.equal(f.$('back-to-demo').hidden,true);assert.equal(f.runs.length,0);
});

test('unavailable public bootstrap offers retry without flashing an authentication gate',async()=>{
 const f=fixture(async()=>({ok:false}));await f.startRemote();
 assert.equal(f.$('login').hidden,true);assert.equal(f.runs.length,0);assert.match(f.errors[0],/wake up/);
});

test('a cancelled startup cannot reconnect after the user moves away',async()=>{
 let resolve;
 const f=fixture(()=>new Promise(done=>{resolve=done;}));const pending=f.startRemote();f.disconnect();
 resolve({ok:true,json:async()=>({access:'public',read_only:true})});await pending;assert.equal(f.runs.length,0);
});

test('public browser refuses mutation calls before making a request',async()=>{
 const apiSource=source.slice(source.indexOf('async function api('),source.indexOf('async function allPages('));let calls=0;
 const api=new Function('fetch',`const publicAccess=true,localAccess=false;const accessHeaders=()=>({});${apiSource};return api;`)(async()=>{calls++;return{ok:true,json:async()=>({items:[]})};});
 await assert.rejects(api('/locations',{method:'POST',body:'{}'}),/administrator/);
 await api('/locations');assert.equal(calls,1);
});
