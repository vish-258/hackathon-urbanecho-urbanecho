import {el,button,field,input,select,badge,empty,errorBox,formatLevel,formatTime,short,condition} from './ui.mjs';
export async function mountManagement(container,ctx){
 let tab='locations', editing=null;
 const heading=el('div','','page-heading');const headingText=el('div');headingText.append(el('div','CONFIGURATION','eyebrow'),el('h1','Management'),el('p','Manage the places, devices, and rules behind your monitoring.'));heading.append(headingText);container.append(heading);
 const tabs=el('div','','tabbar'),body=el('div');container.append(tabs,body);
 const choices=[['locations','Locations'],['devices','Devices'],['thresholds','Thresholds']];
 function paintTabs(){tabs.replaceChildren();for(const[key,label]of choices)tabs.append(button(label,()=>{tab=key;editing=null;render()},tab===key?'active':''));}
 function render(){paintTabs();body.replaceChildren();if(tab==='locations')locations();if(tab==='devices')devices();if(tab==='thresholds')thresholds();}
 async function submit(form,action,success,keepEditing=null){const feedback=form.querySelector('.feedback');feedback.replaceChildren();const btn=form.querySelector('[type=submit]');btn.disabled=true;try{await action();await ctx.refresh();ctx.notify(success);editing=keepEditing;render();}catch(err){feedback.append(errorBox(err.message));}finally{btn.disabled=false;}}
 function formShell(title,description){const p=el('section','','panel'),inner=el('div','','panel-body'),f=el('form');inner.append(el('h2',title),el('p',description,'muted small'));f.append(el('div','','feedback'));inner.append(f);p.append(inner);return{p,f};}
 function saveButton(f,label){const actions=el('div','','form-actions'),save=button(label,null,'primary');save.type='submit';actions.append(save);if(editing)actions.append(button('Cancel',()=>{editing=null;render()}));f.append(actions);}
 function rules(value={threshold_type:'spl_z_leq',threshold_value:60,interval_seconds:1,recovery_count:3}){const group=el('div','','form-grid'),method=select([['spl_z_leq','dB SPL (Z) · calibrated'],['dbfs_rms','dBFS · digital level']],value.threshold_type),level=input('number',value.threshold_value,{required:true,step:'any'}),interval=input('number',value.interval_seconds,{required:true,min:'1',max:'60',step:'1'}),recovery=input('number',value.recovery_count||3,{required:true,min:'1',max:'100',step:'1'});function bounds(){level.min=method.value==='dbfs_rms'?'-200':'-100';level.max=method.value==='dbfs_rms'?'0':'200';}bounds();method.onchange=bounds;group.append(field('Measurement unit',method),field('Threshold',level),field('Reading interval (seconds)',interval),field('Normal readings to recover',recovery));return{group,values:()=>({threshold_type:method.value,threshold_value:Number(level.value),interval_seconds:Number(interval.value),recovery_count:Number(recovery.value)})};}
 function locations(){const layout=el('div','','management-layout');body.append(layout);const list=el('section','','panel');list.append(el('div','Registered locations','panel-head'));const rows=el('div','','management-list');for(const loc of ctx.locations){const row=el('div','','management-item'),text=el('div');text.append(el('h3',loc.name),el('p',`${loc.latitude.toFixed(5)}, ${loc.longitude.toFixed(5)} · ${loc.timezone}`),el('p',formatLevel(loc.current_threshold?.threshold_value,loc.current_threshold?.threshold_type)));row.append(text,button('Edit',()=>{editing=loc.id;render()}));rows.append(row);}if(!ctx.locations.length)rows.append(empty('No locations yet','Create your first monitoring location.'));list.append(rows);layout.append(list);
 const loc=ctx.locations.find(l=>l.id===editing),{p,f}=formShell(loc?'Edit location':'Create location',loc?'Changes apply from now. Previous recordings keep their original location details. An open incident on an old assignment will close as an assignment change when the next eligible reading arrives.':'Choose a location and its first noise threshold.');const grid=el('div','','form-grid'),name=input('text',loc?.name||'',{required:true,maxLength:200}),lat=input('number',loc?.latitude??'',{required:true,min:'-90',max:'90',step:'any'}),lon=input('number',loc?.longitude??'',{required:true,min:'-180',max:'180',step:'any'}),zone=input('text',loc?.timezone||'Asia/Kolkata',{required:true,maxLength:100});grid.append(field('Location name',name),field('Timezone (e.g. Asia/Kolkata)',zone),field('Latitude',lat),field('Longitude',lon));f.append(grid);let rule;if(!loc){rule=rules();f.append(el('h3','Initial threshold','form-section-heading'),rule.group,el('p','SPL (Z) requires valid microphone calibration. Uncalibrated recordings are retained but cannot confirm an SPL breach.','muted small'));}saveButton(f,loc?'Save location':'Create location');f.onsubmit=e=>{e.preventDefault();submit(f,()=>ctx.api(loc?`/locations/${loc.id}`:'/locations',{method:loc?'PATCH':'POST',body:JSON.stringify({name:name.value.trim(),latitude:Number(lat.value),longitude:Number(lon.value),timezone:zone.value.trim(),...(loc?{expected_version:loc.configuration_version}:rule.values())})}),loc?'Location updated. History preserved.':'Location created.');};layout.append(p);}
 function devices(){
  const mapping=el('section','','panel');
  mapping.append(el('div','Device-to-location mapping','panel-head'));
  const wrap=el('div','','view-table-wrap');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Device IDs and assigned geographic locations');
  const table=el('table','','view-table'),head=el('thead'),header=el('tr'),rows=el('tbody');
  table.append(el('caption','Device IDs and assigned geographic locations','sr-only'));
  for(const title of ['Device ID','Location','Coordinates','Status','Mapping']){const th=el('th',title);th.scope='col';header.append(th);}head.append(header);
  for(const d of ctx.devices){
   const row=el('tr'),identity=el('td'),place=el('td'),coordinates=el('td'),status=el('td'),actions=el('td');
   const loc=ctx.locations.find(l=>l.id===d.location_id),current=ctx.state.locations.get(d.location_id);
   const fresh=current?.streams.some(s=>s.device_id===d.id&&s.assignment_id===d.current_assignment_id&&s.data_status==='fresh');
   identity.append(el('strong',d.external_id||d.id),el('p',d.microphone_model,'muted small'));
   place.textContent=loc?.name||'Unassigned';
   coordinates.append(el('div',loc?`${loc.latitude.toFixed(5)}, ${loc.longitude.toFixed(5)}`:'—'),el('small',loc?.timezone||'','muted'));
   status.append(badge(!d.enabled?'Disabled':fresh?'Reporting':d.last_contact_at?'No recent data':'Not connected',d.enabled&&fresh?'good':'stale'),el('p',`Last contact: ${formatTime(d.last_contact_at)}`,'muted small'));
   actions.append(button('Edit mapping',()=>{editing=d.id;render()}));row.append(identity,place,coordinates,status,actions);rows.append(row);
  }
  table.append(head,rows);wrap.append(table);mapping.append(wrap);
  if(!ctx.devices.length)mapping.append(empty('No devices yet','Register the ID sent by your device and choose its location below.'));
  body.append(mapping);
  const device=ctx.devices.find(d=>d.id===editing),{p,f}=formShell(device?'Edit device mapping':'Register device',device?'Location changes apply from now. Earlier recordings retain their original location.':'Match the ID configured on the board to a geographic location. A private device credential is shown once after registration.');
  const grid=el('div','','form-grid'),code=input('text',device?.external_id||device?.id||'',{required:true,maxLength:device?36:32,readOnly:!!device,placeholder:'UE-001',autocomplete:'off'});
  const location=select(ctx.locations.map(l=>[l.id,l.name]),device?.location_id||ctx.locations[0]?.id||'');location.required=true;
  grid.append(field('Device ID (sent by the board)',code),field('Assigned location',location));
  const model=input('text','INMP441',{required:true,maxLength:100}),enabled=select([['true','Enabled'],['false','Disabled']],String(device?.enabled??true));
  grid.append(device?field('Device enabled',enabled):field('Microphone model',model));f.append(grid);
  f.append(el('p',device?'The device ID stays fixed. Change the location here without updating the board.':'Use 1–32 letters, numbers, hyphens or underscores. IDs are case-sensitive and must be unique. Create the location first in the Locations tab.','muted small'));
  f.append(el('p','New physical devices start without sound-pressure calibration. Digital levels can be tested with a dBFS threshold; SPL thresholds require measured calibration.','muted small'));
  saveButton(f,device?'Save mapping':'Register device');if(!ctx.locations.length)f.querySelector('[type=submit]').disabled=true;
  f.onsubmit=async e=>{
   e.preventDefault();
   if(device){submit(f,()=>ctx.api(`/devices/${device.id}`,{method:'PATCH',body:JSON.stringify({expected_revision:device.config_revision,location_id:location.value,enabled:enabled.value==='true'})}),'Device mapping updated. History preserved.');return;}
   const feedback=f.querySelector('.feedback'),submitButton=f.querySelector('[type=submit]');feedback.replaceChildren();submitButton.disabled=true;
   try{
    const created=await ctx.api('/devices',{method:'POST',body:JSON.stringify({external_id:code.value.trim(),location_id:location.value,microphone_model:model.value.trim()})});
    // Keep the one-time credential available even if refreshing the list fails.
    try{await ctx.refresh();}catch{ctx.notify('Device registered. Reopen Management to refresh the list.');}
    render();
    const box=el('section','','provisioning'),credential=input('password',created.token,{readOnly:true,autocomplete:'off'});
    box.append(el('h2','Save this device credential'),el('p',`Device ID: ${created.external_id||created.id}. Copy this ID and credential into the board’s private configuration. The credential will not be shown again.`),field('Device token',credential));
    let visible=false;
    box.append(button('Show / hide',()=>{visible=!visible;credential.type=visible?'text':'password'}),button('Copy token',async()=>{try{await navigator.clipboard.writeText(credential.value);ctx.notify('Device credential copied.');}catch{credential.type='text';credential.select();ctx.notify('Select and copy the device credential.');}}),button('I saved it — close',()=>{credential.value='';box.remove()}));
    body.prepend(box);created.token='';
   }catch(err){feedback.append(errorBox(err.message));}finally{submitButton.disabled=false;}
  };
  body.append(p);
 }
 async function thresholds(){const {p,f}=formShell('Location thresholds','Each change creates a new version. Earlier incidents keep the rule that applied when they occurred.');body.append(p);const location=select(ctx.locations.map(l=>[l.id,l.name]),editing||ctx.locations[0]?.id||'');f.append(field('Location',location));const content=el('div');f.append(content);let ruleData=null,requestNumber=0;async function load(){const request=++requestNumber,locationId=location.value;content.replaceChildren(el('p','Loading threshold…','muted'));try{const result=await ctx.api(`/locations/${locationId}/threshold`,{signal:ctx.signal});if(ctx.signal.aborted||request!==requestNumber)return;ruleData=result;content.replaceChildren();const savedRevision=ruleData.latest_revision,rule=rules(ruleData.latest||{});content.append(rule.group,el('p',`Latest saved revision: ${ruleData.latest_revision}. Current rule: ${formatLevel(ruleData.current?.threshold_value,ruleData.current?.threshold_type)}. Changes take effect immediately. Existing active incidents transition when a new eligible reading arrives.`,'muted small'));saveButton(content,'Save threshold');f.onsubmit=e=>{e.preventDefault();submit(f,()=>ctx.api(`/locations/${locationId}/threshold`,{method:'PATCH',body:JSON.stringify({...rule.values(),expected_revision:savedRevision})}),'Threshold saved as a new version.',locationId);};}catch(err){if(request===requestNumber&&!ctx.signal.aborted)content.replaceChildren(errorBox(err.message));}}location.onchange=load;if(location.value)load();else content.append(empty('Create a location first','Each location has its own versioned threshold.'));}
 render();return()=>body.replaceChildren();
}
