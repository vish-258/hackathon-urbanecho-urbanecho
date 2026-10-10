import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {el,button,errorBox} from '../app/static/application/ui.mjs';

// Exercise the application's route lifecycle without starting authentication
// or its long-lived event stream. Views can pause midway through their mount.
const source=await readFile(new URL('../app/static/application/app.mjs',import.meta.url),'utf8');
const routeSource=source.slice(source.indexOf('async function renderRoute('),source.indexOf('function mountOverview(container){'));

class Element{
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.dataset={};this.className='';this._text='';this.hidden=false;this.listeners={};this.focusCount=0;this.classList={toggle:(name,active)=>{const tokens=new Set(this.className.split(' ').filter(Boolean));if(active)tokens.add(name);else tokens.delete(name);this.className=[...tokens].join(' ');}};}
 set textContent(value){this.replaceChildren();this._text=String(value);}
 get textContent(){return this._text+this.children.map(child=>child.textContent).join(' ');}
 append(...children){for(const child of children){child.parentNode?.removeChild(child);child.parentNode=this;this.children.push(child);}}
 removeChild(child){if(child.contains(document.activeElement))document.activeElement=document.body;this.children=this.children.filter(node=>node!==child);child.parentNode=null;}
 remove(){this.parentNode?.removeChild(this);}
 replaceChildren(...children){for(const child of [...this.children])this.removeChild(child);this._text='';this.append(...children);}
 all(){return this.children.flatMap(child=>[child,...child.all()]);}
 contains(node){return this===node||this.all().includes(node);}
 querySelector(selector){return this.all().find(node=>selector.startsWith('.')?node.className.split(' ').includes(selector.slice(1)):node.tag===selector)||null;}
 setAttribute(name,value){this.attributes[name]=String(value);}
 removeAttribute(name){delete this.attributes[name];}
 addEventListener(name,callback){this.listeners[name]=callback;}
 focus(){document.activeElement=this;this.focusCount++;}
}

function fixture(t){
 const previous=globalThis.document,body=new Element('body'),main=new Element('main'),content=new Element('div'),pageLabel=new Element('b');
 const nav=['overview','incidents','reports','management'].map(page=>{const node=new Element('a');node.dataset.page=page;return node;});
 const doc={body,title:'UrbanEcho',activeElement:body,createElement:tag=>new Element(tag),querySelectorAll:selector=>selector==='nav a'?nav:[]};
 globalThis.document=doc;t.after(()=>{globalThis.document=previous;});
 main.append(content);body.append(...nav,main);
 const location={hash:'#overview'},ctx={},mounts=[],cleaned=[];
 let mount=async()=>{},metadata=async()=>{};
 const appendHeading=(target,label)=>target.append(el('h1',label));
 const values={el,button,errorBox,document:doc,location,ctx,AbortController,URLSearchParams,$:id=>({'main':main,'content':content,'page-label':pageLabel})[id],
  refreshMetadata:()=>metadata(),
  mountOverview:target=>{appendHeading(target,'Noise, made visible.');return{destroy:()=>cleaned.push('overview')};},
  mountView:async(target,route,context)=>{appendHeading(target,route.page);mounts.push({target,route,context});await mount(target,route,context);return()=>cleaned.push(route.page);},
  mountManagement:async(target,context)=>{appendHeading(target,'Management');mounts.push({target,route:{page:'management'},context});await mount(target,{page:'management'},context);return()=>cleaned.push('management');}};
 const render=new Function(...Object.keys(values),`let activeRoute=null,routeController=null,routeCleanup=null,overview=null;${routeSource};return renderRoute;`)(...Object.values(values));
 return{doc,body,main,content,nav,location,ctx,mounts,cleaned,render,setMount:value=>{mount=value;},setMetadata:value=>{metadata=value;}};
}

test('every route has an accurate title and explicit page navigation focuses its heading',async t=>{
 const f=fixture(t);f.nav[0].focus();await f.render();
 assert.equal(f.doc.title,'Overview · UrbanEcho');assert.equal(f.doc.activeElement,f.nav[0],'initial connection does not move focus');
 for(const [path,title,active] of [['incidents','Incident history','incidents'],['incident/one','Incident details','incidents'],['reports','Daily reports','reports'],['management','Management','management'],['location/one','Location details','overview'],['overview','Overview','overview']]){
  f.location.hash=`#${path}`;await f.render({navigation:true});
  assert.equal(f.doc.title,`${title} · UrbanEcho`);assert.equal(f.doc.activeElement,f.content.querySelector('h1'));assert.equal(f.doc.activeElement.tabIndex,-1);
  assert.deepEqual(f.nav.filter(node=>node.attributes['aria-current']==='page').map(node=>node.dataset.page),[active]);
 }
});

test('query filters and background reconnects do not focus the replacement view',async t=>{
 const f=fixture(t);f.location.hash='#reports?date=2026-10-09';await f.render();
 f.nav[2].focus();f.location.hash='#/reports?date=2026-10-10';await f.render({navigation:true});
 assert.equal(f.doc.activeElement,f.nav[2]);assert.equal(f.content.querySelector('h1').focusCount,0);
 await f.render();assert.equal(f.doc.activeElement,f.nav[2]);assert.equal(f.content.querySelector('h1').focusCount,0);
 f.location.hash='#location/one';await f.render({navigation:true});f.location.hash='#location/two';await f.render({navigation:true});
 assert.equal(f.doc.activeElement,f.content.querySelector('h1'),'a different location is a distinct page');
});

test('initial content stays hidden while loading and is revealed without a loading row above it',async t=>{
 const f=fixture(t);await f.render();let release;
 f.setMount(()=>new Promise(resolve=>{release=resolve;}));f.location.hash='#incidents';const pending=f.render({navigation:true});
 const target=f.mounts.at(-1).target,loading=f.content.querySelector('.loading');
 assert.equal(target.hidden,true);assert.equal(loading.attributes.role,'status');assert.equal(loading.textContent,'Loading incident history…');
 assert.ok(target.querySelector('h1'),'a partial view already exists but is not visible');
 release();await pending;
 assert.equal(target.hidden,false);assert.equal(f.content.querySelector('.loading'),null);assert.equal(f.doc.activeElement,target.querySelector('h1'));
});

test('a newer route wins over a slow mount and disposes the abandoned view',async t=>{
 const f=fixture(t);await f.render();let release;
 f.setMount((target,route)=>route.page==='incidents'?new Promise(resolve=>{release=resolve;}):undefined);
 f.location.hash='#incidents';const previous=f.render({navigation:true}),abandoned=f.mounts.at(-1);
 f.location.hash='#reports';await f.render({navigation:true});const heading=f.doc.activeElement;
 assert.equal(abandoned.context.signal.aborted,true);release();await previous;
 assert.equal(f.doc.title,'Daily reports · UrbanEcho');assert.equal(f.doc.activeElement,heading);assert.equal(f.content.querySelector('h1'),heading);assert.ok(f.cleaned.includes('incidents'));assert.equal(abandoned.target.hidden,true);
});

test('navigation does not reclaim focus after another action during loading',async t=>{
 const f=fixture(t);await f.render();let release;
 f.setMount(()=>new Promise(resolve=>{release=resolve;}));f.location.hash='#reports';const pending=f.render({navigation:true});
 f.nav[3].focus();release();await pending;
 assert.equal(f.doc.activeElement,f.nav[3]);assert.equal(f.content.querySelector('h1').focusCount,0);
});

test('failed routes expose their error and a retry without leaving hidden partial content',async t=>{
 const f=fixture(t);await f.render();f.setMount(async()=>{throw new Error('Request unavailable. Please try again.');});
 f.location.hash='#reports';await f.render({navigation:true});
 assert.equal(f.content.querySelector('.error-box').attributes.role,'alert');assert.equal(f.content.querySelector('.loading'),null);assert.equal(f.doc.activeElement,f.main);
 const retry=f.content.querySelector('button');assert.equal(retry.textContent,'Try again');f.setMount(async()=>{});await retry.listeners.click();
 assert.equal(f.content.querySelector('.error-box'),null);assert.equal(f.content.children[0].hidden,false);
});
