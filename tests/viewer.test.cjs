/* Controller contract checks using Node's test runner. These do not replace WebGL QA. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const controller = fs.readFileSync(path.join(__dirname,
  '../src/soil_sensing/visualization/assets/viewer.js'), 'utf8');

function payload() {
  const scene = {
    plot_id:'fixture', survey_id:'test-session', crs:'EPSG:32632', origin_xy_m:[500000,6600000],
    diagnostic:false, boundary:[[0,0],[10,0],[10,10],[0,10]], scale_bar_m:10,
    quality:{excluded_positions:1, excluded_windows:2},
    curtains:[{xy:[[0,0],[1,0]],vertical:[0,10],amplitude:[[1,null],[3,4]],
      trace_ids:['source/a:1','source/a:2'],position_status:['measured','interpolated'],quality_flags:[0,256]}],
    observations:[[{xy:[0,0],value:2,trace_id:'source/a:1',position_status:'measured',quality_flags:0}],
      [{xy:[1,0],value:3,trace_id:'source/a:2',position_status:'interpolated',quality_flags:256}]],
    grid:{x:[.5,1.5],y:[.5,1.5],value:[[[2,null],[2,2]],[[3,3],[null,3]]],
      distance:[[[1,null],[1,1]],[[1,1],[null,1]]],count:[[[3,0],[3,3]],[[3,3],[0,3]]],coverage:[.75,.75]}
  };
  return {scene_id:'fixture',synthetic:true,gates:[[0,5],[5,10]],gate_unit:'ns',
    vertical_axis:'recording_time',attribute_unit:'ADC counts',method:'linear',max_distance_m:5,
    amplitude_limit:4,rms_limit:3,view_defaults:{gate:0},
    scenes:Array.from({length:4},(_,i)=>({...structuredClone(scene),plot_id:'fixture_'+i}))};
}

async function setup(data=payload()) {
  const controls = new Map(), renders=[];
  function element(id) {
    if (!controls.has(id)) controls.set(id,{value:'',checked:false,textContent:'',
      options:[{},{}],handlers:{},dataset:{},append(){},on(){},click(){},
      addEventListener(name,fn){this.handlers[name]=fn;}});
    return controls.get(id);
  }
  for (const id of ['boundary','tracks','curtains','interpolated']) element(id).checked=true;
  element('scene-data').textContent=JSON.stringify(data);
  element('mode').value='single';element('samples').value='all';element('plotSelect').value='0';
  element('exaggeration').value='1';
  const context={document:{getElementById:element,createElement:()=>({click(){}})},window:{},
    performance:{now:()=>0},setInterval:()=>1,clearInterval(){},Blob,URL,
    Plotly:{react:async (el,traces,layout,config)=>{renders.push({traces,layout,config,method:'react'});},
      restyle:async (el,updates,indices)=>{
        const previous=renders.at(-1), traces=structuredClone(previous.traces);
        indices.forEach((index,j)=>Object.entries(updates).forEach(([key,values])=>{
          if(key==='marker.color')traces[index].marker.color=values[j];
          else traces[index][key]=values[j];
        }));
        renders.push({...previous,traces,method:'restyle'});
      }}};
  vm.runInNewContext(controller,context);
  await new Promise(resolve=>setImmediate(resolve));
  return {controls,renders,change:async(id,value,event='change')=>{
    const control=element(id);control.value=value;await control.handlers[event]();
    await new Promise(resolve=>setImmediate(resolve));
  },click:async id=>{await element(id).onclick();await new Promise(resolve=>setImmediate(resolve));}};
}

test('unsupported cells and invalid amplitudes remove geometry, not just color',async()=>{
  const {renders}=await setup();
  const traces=renders.at(-1).traces;
  const slice=traces.find(t=>t.type==='surface'&&t.colorscale==='Viridis');
  assert.equal(slice.z[0][1],null);
  assert.equal(slice.z[0][0],-2.5);
  const curtain=traces.find(t=>t.type==='surface'&&t.colorscale==='RdBu');
  assert.equal(curtain.z[0][1],null);
  assert.equal(curtain.customdata[1][1][0],'source/a:2');
});

test('slice, plot and camera controls update arrays and explicit units',async()=>{
  const s=await setup();
  await s.change('slice','1','input');
  assert.equal(s.renders.at(-1).method,'restyle');
  assert.deepEqual(Array.from(s.renders[0].config.modeBarButtonsToRemove),['sendChartToCloud']);
  assert.equal(JSON.parse(s.controls.get('timing').dataset.benchmark).renders.length,2);
  assert.match(s.controls.get('interval').textContent,/5 – 10 ns/);
  const slice=s.renders.at(-1).traces.find(t=>t.type==='surface'&&t.colorscale==='Viridis');
  assert.equal(slice.z[0][0],-7.5);
  assert.equal(slice.z[1][0],null);
  await s.change('plotSelect','3');
  await s.click('top');
  assert.equal(s.renders.at(-1).layout.scene.camera.eye.z,2.6);
  assert.equal(s.renders.at(-1).layout.scene.uirevision,1);
  await s.click('reset');
  assert.equal(s.renders.at(-1).layout.scene.camera.eye.z,1.2);
  assert.equal(s.renders.at(-1).layout.scene.uirevision,2);
});

test('overview renders independent frames with a shared quantitative scale',async()=>{
  const s=await setup();await s.change('mode','overview');
  const result=s.renders.at(-1);
  assert.equal(result.layout.annotations.length,4);
  const surfaces=result.traces.filter(t=>t.type==='heatmap');
  assert.equal(surfaces.length,4);
  assert.ok(surfaces.every(t=>t.zmax===3&&t.zmin===0));
  assert.equal(new Set(surfaces.map(t=>t.xaxis)).size,4);
  assert.equal(surfaces[0].z[0][1],null);
  assert.ok(s.controls.get('top').disabled);
  await s.change('slice','1','input');
  assert.equal(s.renders.at(-1).traces.find(t=>t.type==='heatmap').z[1][0],null);
});

test('position toggle masks GPS-interpolated curtains without recomputing grids',async()=>{
  const s=await setup();await s.change('samples','measured');
  const traces=s.renders.at(-1).traces;
  const curtain=traces.find(t=>t.type==='surface'&&t.colorscale==='RdBu');
  assert.equal(curtain.x[0][1],null);
  assert.equal(curtain.z[1][1],null);
  assert.equal(traces.find(t=>t.type==='surface'&&t.colorscale==='Viridis').surfacecolor[1][1],2);
});

test('layer toggles and support distance use separate units',async()=>{
  const s=await setup();s.controls.get('curtains').checked=false;
  await s.change('curtains','');
  assert.ok(!s.renders.at(-1).traces.some(t=>t.colorscale==='RdBu'));
  s.controls.get('coverage').checked=true;await s.change('coverage','');
  const support=s.renders.at(-1).traces.find(t=>t.colorscale==='Cividis');
  assert.equal(support.cmax,5);assert.equal(support.colorbar.title.text,'Support (m)');
});

test('diagnostic review never enables a geographic overview',async()=>{
  const data=payload();data.scenes.forEach(s=>{
    s.diagnostic=true;s.grid=null;s.crs=null;s.coordinate_mode='odometer';
  });
  const s=await setup(data);
  assert.ok(s.controls.get('mode').options[1].disabled);
  assert.match(s.controls.get('empty').textContent,/reviewed plot geometry/);
});

test('raw GPS diagnostic labels approximate geometry and filters interpolated positions',async()=>{
  const data=payload();data.synthetic=false;data.scenes=data.scenes.slice(0,1);
  Object.assign(data.scenes[0],{diagnostic:true,grid:null,crs:null,
    coordinate_mode:'raw_gps_relative',coordinate_note:'unverified'});
  const s=await setup(data);
  assert.match(s.controls.get('badge').textContent,/UNVERIFIED GPS LAYOUT/);
  assert.match(s.renders.at(-1).layout.scene.xaxis.title.text,/Approx. east offset/);
  assert.match(s.controls.get('status').textContent,/raw NMEA/);
  await s.change('samples','measured');
  const curtain=s.renders.at(-1).traces.find(t=>t.meta?.layer==='curtain');
  assert.equal(curtain.x[0][1],null);
});

test('combined curtains and tracks keep explicit gaps and trace identity',async()=>{
  const data=payload();
  data.scenes[0].curtains.push(structuredClone(data.scenes[0].curtains[0]));
  const s=await setup(data), traces=s.renders.at(-1).traces;
  const curtains=traces.filter(t=>t.meta?.layer==='curtain');
  assert.equal(curtains.length,1);
  assert.equal(curtains[0].z[1][2],null);
  assert.equal(curtains[0].x[1][2],null);
  assert.equal(curtains[0].customdata[1][3][0],'source/a:1');
  const tracks=traces.filter(t=>t.meta?.layer==='tracks');
  assert.equal(tracks.length,1);
  assert.equal(tracks[0].x[2],null);
  assert.equal(tracks[0].customdata[3],'source/a:1');
});
