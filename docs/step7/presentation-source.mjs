// UrbanEcho editable presentation. Requires @oai/artifact-tool.
// Run from a directory with that package available, passing the content folder.
// node presentation-source.mjs /path/to/docs/step7 /path/to/private/build
import fs from 'node:fs/promises';
import path from 'node:path';
import { Presentation, PresentationFile } from '@oai/artifact-tool';

const CONTENT = path.resolve(process.argv[2] ?? '.');
const BUILD = path.resolve(process.argv[3] ?? './build');
const manifest = JSON.parse(await fs.readFile(path.join(CONTENT, 'presentation-data.json'), 'utf8'));
await fs.mkdir(BUILD, { recursive: true });
const P = Presentation.create({ slideSize: { width: 1280, height: 720 } });
const C = { bg:'#F2F5F0', ink:'#143E38', muted:'#536860', line:'#CCD8CF', green:'#267463', pale:'#E2E9DA', white:'#FFFFFF', amber:'#97521F' };
const FONT = 'Noto Sans';
let n = 0;

function text(s, value, x, y, w, h, size=26, bold=false, color=C.ink, align='left') {
  const obj=s.shapes.add({ geometry:'textbox', name:`text-${n++}`, position:{left:x,top:y,width:w,height:h}, fill:'none', line:{fill:'none',width:0} });
  obj.text=value;
  obj.text.style={fontSize:size,bold,typeface:FONT,color,alignment:align,verticalAlignment:'top',autoFit:'none',wrap:'square',insets:0};
  return obj;
}
function slide(title, note, dark=false) {
  const s=P.slides.add();s.background.fill=dark?C.ink:C.bg;
  if(title)text(s,title,64,46,1152,76,44,true,dark?C.white:C.ink);
  if(!dark)text(s,String(P.slides.items.length).padStart(2,'0'),1170,662,46,24,17,false,C.muted,'right');
  s.speakerNotes.text=note;
  return s;
}
function label(s,v,x,y,w=440,c=C.green){text(s,v,x,y,w,32,22,true,c);}
function line(s,x,y,w,c=C.line){s.shapes.add({geometry:'line',position:{left:x,top:y,width:w,height:0},line:{fill:c,width:1},fill:'none'});}
function table(s,values,x,y,w,h,widths,size=25) {
  const rows=values.length,cols=values[0].length;
  const t=s.tables.add({rows,columns:cols,left:x,top:y,width:w,height:h,columnWidths:widths,values});
  t.borders.assign({style:'solid',fill:C.line,width:0.6});
  t.cells.block({row:0,column:0,rowCount:rows,columnCount:cols}).assign({fill:C.bg,textStyle:{fontSize:size,typeface:FONT,color:C.ink},margins:{top:10,right:16,bottom:10,left:16},anchor:'center'});
  t.cells.block({row:0,column:0,rowCount:1,columnCount:cols}).assign({fill:C.ink,textStyle:{fontSize:size-1,typeface:FONT,color:C.white,bold:true}});
  return t;
}
function node(s,v,x,y,w,h=86,pending=false) {
  const o=s.shapes.add({geometry:'rect',name:v,position:{left:x,top:y,width:w,height:h},fill:pending?C.bg:C.pale,line:{fill:pending?C.amber:C.green,width:1.5,style:pending?'dashed':'solid'}});
  o.text=v;o.text.style={fontSize:24,typeface:FONT,color:pending?C.amber:C.ink,bold:true,alignment:'center',verticalAlignment:'middle',insets:10};return o;
}
function connect(s,a,b,from='right',to='left',pending=false){s.shapes.connect(a,b,{kind:'straight',fromSide:from,toSide:to,line:{fill:pending?C.amber:C.green,width:2,style:pending?'dashed':'solid'},tail:{type:'triangle',width:'sm',length:'sm'}});}
async function screenshot(s, key, x,y,w,h) {
  const item=manifest.screenshots[key];
  if(!item) throw new Error(`Missing actual screenshot: ${key}`);
  const bytes=await fs.readFile(path.resolve(CONTENT,item.file));
  // Size the native image frame to the crop's real aspect ratio. This keeps
  // app screenshots undistorted while embedding the full original image.
  const crop=item.crop??{left:0,top:0,right:0,bottom:0};
  const sourceW=item.width*(1-crop.left-crop.right);
  const sourceH=item.height*(1-crop.top-crop.bottom);
  const scale=Math.min(w/sourceW,h/sourceH);
  const iw=sourceW*scale,ih=sourceH*scale;
  s.images.add({blob:bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),contentType:item.contentType,alt:item.alt,fit:'contain',...(item.crop?{crop:item.crop}:{}),position:{left:x+(w-iw)/2,top:y+(h-ih)/2,width:iw,height:ih}});
}
const prior='Source: STEP6-VERIFICATION.md, 9 October 2026. These are previously recorded checks. The Step 7 CURRENT-STATUS.md distinguishes new checks from this baseline.';

// 1. Cover
{
  const s=slide(null,'UrbanEcho working local prototype for residential community management. Planning date: 9 October 2026.',true);
  text(s,'UrbanEcho',64,126,1152,126,92,true,C.white);
  text(s,'Noise monitoring for\nresidential communities',68,300,1080,130,44,false,C.white);
  text(s,'Working local prototype\n9 October 2026',70,552,750,74,26,false,'#C6D8CC');
}
// 2. User and problem
{
  const s=slide('A shared record for community managers','Planning hypothesis, not validated customer research. See BUSINESS-CASE.md for buyer, pilot metrics and adoption assumptions. UrbanEcho cannot establish who caused a sound or prove regulatory compliance.');
  label(s,'INTENDED USER',64,152);
  text(s,'The manager of a residential society or gated community',64,206,518,152,36,true);
  text(s,'A residents’ association or property manager would sponsor an initial pilot.',64,401,518,110,27,false,C.muted);
  label(s,'PROPOSED USE CASE',680,152);
  text(s,'Review recurring noise near shared areas using location, time and saved incident history.',680,206,536,154,32);
  line(s,680,402,536);
  text(s,'Pilot question\nDoes a common record help staff respond sooner and close complaints with clearer evidence?',680,433,536,168,28);
}
// 3. Actual application
{
  const s=slide('The working application','Actual local UrbanEcho screenshot captured during Step 7 on 9 October 2026. All demonstration locations, recordings and calibration shown here are simulated. The application runs at http://localhost:8000/app. See CURRENT-STATUS.md and DEMO-WALKTHROUGH.md.');
  await screenshot(s,'overview',624,137,592,489);
  text(s,'A map connects each reading to its registered location.',64,157,488,107,30,true);
  text(s,'Latest levels and device freshness\n\nSaved incidents and notifications\n\nLocation history and daily reports',64,314,502,253,27);
  text(s,'Three simulated devices report. Older demo devices remain silent, so missing-data indicators stay visible.',64,622,1100,58,21,false,C.muted);
}
// 4. Architecture
{
  const s=slide('The recording and reporting flow','Source: app/main.py, app/audio.py, app/processing.py, app/evaluation.py, app/daily.py, app/daily_jobs.py, app/device_api.py, compose.yaml and STEP6-VERIFICATION.md. WAV bytes live in a persistent volume. PostgreSQL/PostGIS stores metadata, measurements, incidents and summaries. Physical device uses the same upload contract through an optional private-LAN HTTPS listener. Listener is prepared but disabled.');
  const sim=node(s,'Simulator\n3 registered devices',64,172,256,142);
  const api=node(s,'FastAPI\naudio upload',400,172,240,142);
  const saved=node(s,'Worker calculates levels\nSaved WAV + PostgreSQL / PostGIS',720,172,496,142);
  connect(s,sim,api);connect(s,api,saved);
  const hw=node(s,'ESP32 + INMP441\nPhysical test pending',64,362,256,114,true);
  s.shapes.connect(hw,api,{kind:'elbow',fromSide:'right',toSide:'bottom',line:{fill:C.amber,width:2,style:'dashed'},tail:{type:'triangle',width:'sm',length:'sm'}});
  const live=node(s,'Thresholds\nand incidents',720,387,226,100);
  const daily=node(s,'Daily\nsummaries',990,387,226,100);
  const app=node(s,'Web application',815,556,306,70);
  for(const target of [live,daily])s.shapes.connect(saved,target,{kind:'elbow',fromSide:'bottom',toSide:'top',line:{fill:C.green,width:2},tail:{type:'triangle',width:'sm',length:'sm'}});
  for(const source of [live,daily])s.shapes.connect(source,app,{kind:'elbow',fromSide:'bottom',toSide:'top',line:{fill:C.green,width:2},tail:{type:'triangle',width:'sm',length:'sm'}});
  text(s,'Live alerts and daily reports\nuse saved server records.',64,590,610,69,25,false,C.muted);
  text(s,'Optional LAN HTTPS\nprepared, disabled',64,494,255,62,21,false,C.amber);
}
// 5. Demonstration
{
  const s=slide('A repeatable noise incident demonstration','Sources: SIMULATOR-VERIFICATION.md, scripts/verify-simulator.py and docs/step7/DEMO-WALKTHROUGH.md. The original runner verifies all three locations. Step 7 walkthrough demonstrates one selected excessive location while other locations stay normal. Threshold comparison uses unrounded values and strict greater-than. Stale status does not resolve an incident. Identical retries preserve recording identity. Screenshot is actual simulated local data.');
  await screenshot(s,'incident',656,150,560,454);
  const entries=[['1','Normal and equal levels','No new incident'],['2','Above and sustained','One incident grows'],['3','Pause the sender','Stale, still unresolved'],['4','Three normal readings','Recovery resolves incident'],['5','Repeat an upload','Same saved recording']];
  for(let i=0;i<entries.length;i++){
    const y=153+i*94;
    text(s,entries[i][0],64,y,40,48,32,true,C.green);
    text(s,entries[i][1],119,y,478,35,27,true);
    text(s,entries[i][2],119,y+39,478,34,23,false,C.muted);
  }
  text(s,'Demonstration inputs and calibration remain clearly marked SIMULATED.',64,638,1050,28,21,false,C.muted);
}
// 6. Daily reporting
{
  const s=slide('Daily reports show recorded coverage','Sources: app/daily.py, app/daily_jobs.py, README.md, DAILY-VERIFICATION.md and docs/step7/CURRENT-STATUS.md. Daily aggregation uses duration-weighted sound energy for compatible eligible measurements. Coverage is the union of usable intervals, not a count of all device-seconds. Local-day boundaries handle timezone and DST. Missing audio is not silence. This report is simulated and partial. Recalculation updates the stored summary identity.');
  await screenshot(s,'daily',650,142,566,492);
  text(s,'Average of usable recorded time',64,155,510,92,32,true);
  text(s,'Minimum and maximum levels\nEligible measurement count\nIncident starts\nUsable duration and coverage',64,291,528,197,28);
  text(s,'Short samples represent only their recorded periods. Missing time never becomes a zero sound level.',64,512,514,106,25,false,C.muted);
  text(s,'Simulated dB SPL (Z). Real hardware starts uncalibrated.',64,649,1090,29,21,false,C.muted);
}
// 7. Verification
{
  const s=slide('Verification evidence',prior+'\n'+manifest.currentChecks.notes);
  table(s,[['Previously recorded: 9 October 2026','Result'],['Backend automated checks','382 passed'],['Frontend checks','34 passed'],['Saved WAV checksums','300 of 300 passed'],['Three-location demonstration','Two complete runs passed']],64,149,1152,323,[812,340],26);
  label(s,'NEW CHECKS IN STEP 7',64,502);
  text(s,manifest.currentChecks.summary,64,547,1152,94,26);
  text(s,'Simulated software evidence. Real-device and acoustic validation remain pending.',64,650,1090,30,20,false,C.muted);
}
// 8. Hardware
{
  const s=slide('ESP32 integration is prepared',prior+'\nSources: firmware/esp32/README.md, docs/HARDWARE-INTEGRATION.md, firmware/esp32/main and user-confirmed wiring. ESP-WROOM-32-based board with INMP441. Exact carrier model/revision and flash capacity still need confirmation. ESP-IDF v5.4.3 target esp32 build/link passed. Physical board is on another computer. No claim of flashing, real captures, sound-pressure accuracy or calibrated monitoring.');
  label(s,'FIRMWARE COMPILED',64,146);
  text(s,'ESP32 + INMP441\nC++ with ESP-IDF',64,191,500,100,36,true);
  text(s,'1 second at 16 kHz\nMono PCM24 WAV uploads\nConfigurable capture/upload intervals',64,330,528,151,27);
  text(s,'Private Wi-Fi settings, device credentials and the server address are still required.',64,516,533,99,24,false,C.muted);
  table(s,[['INMP441','ESP32'],['VDD / GND','3.3 V / GND'],['SCK','GPIO 26'],['WS','GPIO 25'],['SD','GPIO 33'],['L/R','GND']],680,151,536,382,[260,276],25);
  text(s,'Physical integration and acoustic validation pending',680,568,536,71,28,true,C.amber);
}
// 9. Business case
{
  const s=slide('A residential community pilot','Planning assumptions, not achieved sales or proven savings. Source: BUSINESS-CASE.md. '+manifest.business.notes);
  label(s,'INITIAL OFFER',64,154);
  text(s,manifest.business.offer,64,206,526,159,33,true);
  text(s,manifest.business.value,64,411,526,180,26);
  label(s,'FIRST 100 USERS',688,154);
  text(s,manifest.business.users,688,206,528,155,33,true);
  text(s,manifest.business.acquisition,688,411,528,184,26);
  text(s,'Targets and prices are hypotheses to test with community managers.',64,649,1090,28,21,false,C.muted);
}
// 10. Costs
{
  const s=slide('Pilot cost assumptions','Source: BUSINESS-CASE.md and its dated sources. All planning amounts exclude unquoted acoustic calibration and certification. Continuous audio example: 48,044 bytes per one-second, 16 kHz PCM24 WAV × 86,400 × 30 × 3 = 373,590,144,000 bytes = 373.59 decimal GB, and 7,776,000 separate files. Excludes database, backups and filesystem metadata. No retention automation or cloud object-store migration has been implemented. '+manifest.costs.notes);
  table(s,manifest.costs.rows,64,151,1152,350,[410,360,382],24);
  text(s,'Audio storage is a major design choice',64,538,1120,45,31,true);
  text(s,'Three devices recording continuously produce about 374 GB and 7.78 million WAV files per 30 days, before backups and database storage.',64,589,1120,67,25);
}
// 11. Roadmap and readiness
{
  const s=slide('The next 12 months','Proposed development and adoption plan, not committed delivery dates. Source: BUSINESS-CASE.md, CURRENT-STATUS.md and ACCEPTANCE-CHECKLIST.md. Gates depend on actual board tests, acoustic validation, consent/retention decisions, pilot evidence and reliable operation. Physical milestone: real microphone → ESP32 → UrbanEcho → incident alert → daily summary.');
  const phases=manifest.roadmap;
  for(let i=0;i<phases.length;i++){
    const y=151+i*91;
    text(s,phases[i][0],64,y,217,70,28,true,C.green);
    text(s,phases[i][1],315,y,901,67,27);
    if(i<phases.length-1)line(s,64,y+74,1152);
  }
  text(s,'Local simulated demonstration: ready',64,554,1152,40,31,true,C.green);
  text(s,'Physical monitoring: pending board test and calibration',64,603,1152,42,31,true,C.amber);
  text(s,'Next milestone: real microphone, ESP32, UrbanEcho, incident alert and daily summary.',64,658,1130,28,21,false,C.muted);
}

for(const [i,s] of P.slides.items.entries()){
  const png=await P.export({slide:s,format:'png',scale:1});
  await fs.writeFile(path.join(BUILD,`slide-${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await png.arrayBuffer()));
  const layout=await s.export({format:'layout'});
  await fs.writeFile(path.join(BUILD,`slide-${String(i+1).padStart(2,'0')}.layout.json`),await layout.text());
}
await fs.writeFile(path.join(BUILD,'montage.webp'),new Uint8Array(await (await P.export({format:'webp',montage:true,scale:.3})).arrayBuffer()));
await (await PresentationFile.exportPptx(P)).save(path.join(BUILD,'candidate.pptx'));
await fs.writeFile(path.join(BUILD,'presentation-proto.json'),JSON.stringify(P.toProto()));
console.log(`Created ${P.slides.items.length} editable slides in ${BUILD}`);
