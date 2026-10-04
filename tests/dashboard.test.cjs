const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../api/static/sentinel/app.js'), 'utf8');
function app() {
  const timers = [], events = {};
  const context = { localStorage: {getItem: () => null}, document: {hidden:false,addEventListener:(name,fn)=>events[name]=fn},
    window: {addEventListener:()=>{},scrollTo:()=>{}}, setInterval:(fn,ms)=>timers.push({fn,ms}), setTimeout, clearTimeout, console };
  vm.createContext(context); vm.runInContext(source,context);
  const a=context.sentinel(); a.token='qa'; a.calls=[];
  a.sites=[{id:1,name:'Example',enabled:true}];
  a.payloads={'/api/sites':a.sites,'/api/dashboard/problems':[], '/api/security/summary':{critical:0,exploited:0},
    '/api/security/matches':{items:[]},'/api/history?days=7&limit=200':[], '/api/history/summary?days=7':{days:[]}};
  a.api=async url=>{a.calls.push(url);return {ok:url in a.payloads,json:async()=>a.payloads[url]}};
  return {a,timers,events,context};
}
test('entering dashboard fetches current problems and security without visiting security first',async()=>{
  const {a}=app();a.route.page='sites';a.problems=[{site_id:1,kind:'offline',text:'old'}];
  a.route.page='dashboard';a.payloads['/api/dashboard/problems']=[{site_id:1,kind:'dns',text:'temporary'}];
  a.payloads['/api/security/summary']={critical:1,exploited:0};
  await a._onRoute();
  assert(a.calls.includes('/api/dashboard/problems'));assert(a.calls.includes('/api/security/summary'));
  assert.equal(a.attention.length,2);assert.equal(a.attention[0].link,'security');
  assert.equal(a.attention[1].kind,'warn');assert.equal(a.dashError,false);
});
test('a completed check clears resolved warnings on silent dashboard refresh',async()=>{
  const {a}=app();a.problems=[{site_id:1,kind:'failed',text:'old failure'}];a.meta.version='2.28.13';
  await a.load(true);assert.equal(a.attention.length,0);assert(a.dashProblemsLoaded&&a.dashSecurityLoaded);
});
test('a failed first alert request is not treated as a successfully empty dashboard',async()=>{
  const {a}=app();delete a.payloads['/api/dashboard/problems'];await a.loadDashboard();
  assert.equal(a.dashError,true);assert.equal(a.dashProblemsLoaded,false);assert.equal(a.dashBusy,false);
  a.payloads['/api/dashboard/problems']=[];await a.loadDashboard();
  assert.equal(a.dashError,false);assert.equal(a.dashProblemsLoaded,true);
});
test('failed refresh preserves known alerts and a later successful read replaces them',async()=>{
  const {a}=app();a.payloads['/api/dashboard/problems']=[{site_id:1,kind:'domain',text:'expired'}];await a.loadDashboard();
  a.api=async()=>{throw Error('network')};await a.loadDashboard();assert.equal(a.dashError,true);assert.equal(a.attention.length,1);
});
test('critical security warnings stay visible with more than twelve site warnings',()=>{
  const {a}=app();a.sec.summary={critical:2,exploited:1};a.problems=Array.from({length:20},()=>({site_id:1,kind:'php',text:'old PHP'}));
  assert.equal(a.attention.length,12);assert.equal(a.attention[0].link,'security');assert.equal(a.attentionRemaining,9);
  assert.equal(a.attention[1].kind,'info');
});
test('disabled/deleted sites are excluded from dashboard warnings',()=>{
  const {a}=app();a.sites.push({id:2,name:'Disabled',enabled:false});
  a.problems=[1,2,3].map(site_id=>({site_id,kind:'dns',text:'DNS'}));assert.equal(a.attention.length,1);
});
test('a visible dashboard/security refreshes every minute and hidden tabs pause',async()=>{
  const {a,timers,events,context}=app();
  for(const name of ['initTips','initSidebarResize','loadBrand','_readHash','refreshImageToken','loadMeta','load']) a[name]=async()=>{};
  let refreshed=0;a.refreshActiveView=async()=>refreshed++;a.watchChanges=async()=>{};
  await a.init();const tick=timers.find(t=>t.ms===60000);assert(tick);
  tick.fn();await Promise.resolve();assert.equal(refreshed,1);
  context.document.hidden=true;tick.fn();assert.equal(refreshed,1);
  context.document.hidden=false;events.visibilitychange();await Promise.resolve();assert.equal(refreshed,2);
});
test('pending timeout is visible as a warning and is not counted offline',()=>{
  const {a}=app();a.sites[0].status='check_pending';a.sites[0].error='ConnectTimeout';
  a.problems=[{site_id:1,kind:'check',text:'ConnectTimeout'}];
  assert.equal(a.isOff(a.sites[0]),false);assert.equal(a.statusTone(a.sites[0]),'warn');
  assert.equal(a.totStats.off,0);assert.equal(a.totStats.checking,1);assert.equal(a.attention[0].kind,'warn');
});
test('repeated check-all click starts one batch and excludes disabled sites',async()=>{
  const {a,context}=app();a.sites.push({id:2,name:'Off',enabled:false},{id:3,name:'Third',enabled:true});
  context.setTimeout=fn=>{fn();return 1};let grant;const gate=new Promise(r=>grant=r);const calls=[];
  a.say=()=>{};a.load=async()=>{};a.api=async url=>{calls.push(url);if(calls.length===1)await gate;return {ok:true,json:async()=>({status:'ok'})}};
  const first=a.checkAll();assert.equal(a.checkAllBusy,true);await a.checkAll();grant();await first;
  assert.deepEqual(calls,['/api/sites/1/refresh','/api/sites/3/refresh']);assert.equal(a.checkAllBusy,false);
});
test('saving preferences retains update brakes and omits removed resource settings',async()=>{
 const {a}=app();a.prefsLoaded=true;a.prefs.server_limited=['203.0.113.1'];let sent;
 a.api=async(url,opts)=>{sent=JSON.parse(opts.body);return {ok:true,json:async()=>sent}};
 await a.savePrefs();assert.deepEqual(sent.server_limited,['203.0.113.1']);
 for(const key of ['server_metrics_enabled','server_metrics_minutes','server_split'])assert(!(key in sent));
});
test('old server-status bookmark returns to dashboard',()=>{
 const {a,context}=app();context.location={hash:'#/servers'};a._readHash();assert.equal(a.route.page,'dashboard');
 assert.equal(a.loadServerStatus,undefined);
});
