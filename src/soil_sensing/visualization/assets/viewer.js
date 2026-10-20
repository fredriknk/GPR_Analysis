'use strict';
const data = JSON.parse(document.getElementById('scene-data').textContent);
const $ = id => document.getElementById(id);
const toggles = ['boundary','tracks','curtains','measured','interpolated','coverage'];
const cameras = {
  oblique:{eye:{x:1.55,y:-1.75,z:1.2}}, top:{eye:{x:0,y:0,z:2.6},up:{x:0,y:1,z:0}},
  side:{eye:{x:0,y:-2.7,z:.1},up:{x:0,y:0,z:1}}
};
let camera = cameras.oblique, cameraRevision = 0, timer = null, busy = false;
let desired = 0, rendered = -1, initialized = false;
let lastViewSignature = null, lastGate = null;
const metrics = {renders:[], startup_ms:null};
window.explorerMetrics = metrics;
const lineStates = new Map();
function sceneLineIds(s){return [...new Set(s.curtains.map(c=>c.line_id))]}
function defaultLineIds(s){
  const defaults=(s.line_partitions||[]).filter(p=>p.default_visible).flatMap(p=>p.line_ids);
  return defaults.length?defaults:sceneLineIds(s);
}
function lineState(s){
  const index=data.scenes.indexOf(s);
  if(!lineStates.has(index)){
    const partitioned=(s.line_partitions||[]).length>0;
    lineStates.set(index,{selected:new Set(defaultLineIds(s)),preset:partitioned?'default':'all'});
  }
  return lineStates.get(index);
}
function lineName(lineId){try{return JSON.parse(lineId)[1]}catch{return lineId}}
function addOption(select,value,label){
  const option=document.createElement('option');option.value=value;option.textContent=label;select.append(option);
}
function updateLineCount(s){
  const state=lineState(s),total=sceneLineIds(s).length;
  $('lineCount').textContent=state.selected.size+' of '+total+' lines visible';
}
function populateLineControls(index){
  const s=data.scenes[index],state=lineState(s),all=sceneLineIds(s),select=$('lineSubset');
  select.textContent='';
  if((s.line_partitions||[]).length){
    const gridDefault=(s.line_partitions||[]).some(p=>p.default_visible&&p.partition_id.startsWith('grid-'));
    addOption(select,'default',(gridDefault?'Grid partitions only':'Default line set')+
      ' · '+defaultLineIds(s).length+' lines');
  }
  addOption(select,'all','All lines · '+all.length);
  for(const partition of s.line_partitions||[])addOption(select,'partition:'+partition.partition_id,
    partition.label+' · '+partition.line_ids.length+' lines');
  addOption(select,'custom','Custom selection');select.value=state.preset;
  const list=$('lineList');list.textContent='';
  for(const lineId of all){
    const label=document.createElement('label'),input=document.createElement('input');
    const text=document.createElement('span');input.type='checkbox';input.checked=state.selected.has(lineId);
    input.dataset.lineId=lineId;text.textContent=lineName(lineId);label.append(input,text);list.append(label);
    input.addEventListener('change',()=>{
      if(input.checked)state.selected.add(lineId);else state.selected.delete(lineId);
      state.preset='custom';select.value='custom';updateLineCount(s);render();
    });
  }
  updateLineCount(s);
}
function applyLinePreset(s,preset){
  const state=lineState(s),all=sceneLineIds(s);
  let selected=all;
  if(preset==='default')selected=defaultLineIds(s);
  else if(preset==='custom')selected=[...state.selected];
  else if(preset.startsWith('partition:')){
    const id=preset.slice('partition:'.length);
    selected=(s.line_partitions||[]).find(p=>p.partition_id===id)?.line_ids||[];
  }
  state.selected=new Set(selected);state.preset=preset;populateLineControls(data.scenes.indexOf(s));
}
function visibleCurtains(s){const selected=lineState(s).selected;return s.curtains.filter(c=>selected.has(c.line_id))}
function visibleBoundary(s){
  if(s.coordinate_mode!=='raw_gps_relative')return s.boundary;
  const points=visibleCurtains(s).flatMap(c=>c.xy).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));
  if(!points.length)return s.boundary;
  const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
  const lower=[Math.min(...xs),Math.min(...ys)],upper=[Math.max(...xs),Math.max(...ys)];
  const pad=Math.max(1,.04*Math.max(upper[0]-lower[0],upper[1]-lower[1]));
  return [[lower[0]-pad,lower[1]-pad],[upper[0]+pad,lower[1]-pad],
    [upper[0]+pad,upper[1]+pad],[lower[0]-pad,upper[1]+pad]];
}
function scaleBarLength(boundary){
  const width=Math.max(...boundary.map(p=>p[0]))-Math.min(...boundary.map(p=>p[0])),target=width/4;
  return [1,2,5,10,20,50,100].filter(value=>value<=target).at(-1)||1;
}
data.scenes.forEach((s,i)=>{let o=document.createElement('option');o.value=i;
o.textContent=s.plot_id+' / '+(s.survey_id||'session unknown');$('plotSelect').append(o)});
$('slice').max=data.gates.length-1;
$('slice').value=data.view_defaults.gate;
$('badge').textContent=data.synthetic?'SYNTHETIC DATA · DEMONSTRATION':
  data.scenes.some(s=>s.coordinate_mode==='raw_gps_relative')?'UNVERIFIED GPS LAYOUT · DIAGNOSTIC':
  data.scenes.every(s=>s.diagnostic)?'DIAGNOSTIC LINE REVIEW':'REVIEWED PLOT GEOMETRY';
if(data.scenes.some(s=>s.diagnostic))$('mode').options[1].disabled=true;
function trace(scene,type,extra){return Object.assign({type,scene,showlegend:false},extra)}
function layers(s, scene, gate, overview){
  const out=[], mid=(data.gates[gate][0]+data.gates[gate][1])/2;
  let amplitudeScaleShown=false;
  const gps=s.coordinate_mode==='raw_gps_relative';
  const hover=(gps?'Approx. east offset':'East')+' %{x:.1f} m · '+
    (gps?'approx. north offset':'North')+' %{y:.1f} m<extra></extra>';
  if($('boundary').checked){
    const boundary=visibleBoundary(s),b=[...boundary,boundary[0]],bar=scaleBarLength(boundary);
    out.push(trace(scene,'scatter3d',{mode:'lines',x:b.map(p=>p[0]),y:b.map(p=>p[1]),z:b.map(()=>0),
      line:{color:'#8295a7',width:3},hovertemplate:hover}));
    const a=boundary[0];out.push(trace(scene,'scatter3d',{mode:'lines+text',
      x:[a[0],a[0]+bar],y:[a[1],a[1]],z:[0,0],text:['',bar+' m'],
      textposition:'top center',line:{color:'#e8f1f7',width:5},hoverinfo:'skip'}));
  }
  if(!overview){for(const c of visibleCurtains(s)){
    const allowed=c.position_status.map(v=>$('samples').value==='all'||v==='measured'||
      s.coordinate_mode==='odometer');
    const x=c.xy.map((p,i)=>allowed[i]?p[0]:null), y=c.xy.map((p,i)=>allowed[i]?p[1]:null);
    if($('tracks').checked)out.push(trace(scene,'scatter3d',{meta:{layer:'tracks'},mode:'lines',x,y,z:x.map(()=>0),
      connectgaps:false,line:{color:'#75e3c2',width:3},customdata:c.trace_ids,
      hovertemplate:'%{customdata}<br>'+hover}));
    if($('curtains').checked && x.length>1){out.push(trace(scene,'surface',{meta:{layer:'curtain',vertical:c.vertical},
      x:c.vertical.map(()=>x),y:c.vertical.map(()=>y),
      z:c.vertical.map((v,j)=>c.amplitude[j].map((a,i)=>a===null||!allowed[i]?null:-v)),
      surfacecolor:c.amplitude.map(row=>row.map((v,i)=>allowed[i]?v:null)),
      customdata:c.vertical.map((v,j)=>c.trace_ids.map((t,i)=>[
        t,c.position_status[i],c.quality_flags[i],c.depth_standard_uncertainty_m?.[j]??'unknown'])),
      cmin:-data.amplitude_limit,cmax:data.amplitude_limit,colorscale:'RdBu',connectgaps:false,
      showscale:!amplitudeScaleShown,opacity:.78,
      colorbar:{title:{text:'Signed amplitude · '+data.attribute_unit},orientation:'h',
        x:.45,y:-.1,len:.5,thickness:10},
      hovertemplate:'%{customdata[0]}<br>Position: %{customdata[1]}'+
        '<br>Flags: %{customdata[2]}<br>Amplitude %{surfacecolor:.2f} '+data.attribute_unit+
        '<br>Vertical %{z:.1f} '+data.gate_unit+' (negative down)'+
        (data.gate_unit==='m'?'<br>Depth standard uncertainty: %{customdata[3]} m':'')+'<extra></extra>'
    }));amplitudeScaleShown=true;}
  }}
  const grid=s.grid;
  if(grid && ($('interpolated').checked||$('coverage').checked)){
    const coverage=$('coverage').checked, color=coverage?grid.distance[gate]:grid.value[gate];
    out.push(trace(scene,'surface',{meta:{layer:'slice'},x:grid.x,y:grid.y,
      z:grid.value[gate].map(row=>row.map(v=>v===null?null:-mid)),
      surfacecolor:color,cmin:0,cmax:coverage?data.max_distance_m:data.rms_limit,
      colorscale:coverage?'Cividis':'Viridis',connectgaps:false,
      showscale:scene==='scene',colorbar:{title:{text:coverage?'Support (m)':'RMS<br>'+data.attribute_unit},
        thickness:14,len:.65,x:1.02},customdata:grid.count[gate],
      hovertemplate:'East %{x:.1f} m · North %{y:.1f} m<br>'+
        (coverage?'Nearest support %{surfacecolor:.1f} m':'RMS %{surfacecolor:.2f} '+data.attribute_unit)+
        '<br>Contributing traces %{customdata}<br>Method: '+data.method+
        '<br>Gate '+data.gates[gate].join('–')+' '+data.gate_unit+'<extra></extra>'
    }));
  }
  if($('measured').checked){const selected=lineState(s).selected,rows=s.observations[gate].filter(r=>
    selected.has(r.line_id)&&($('samples').value==='all'||r.position_status==='measured'||
      s.coordinate_mode==='odometer'));
    out.push(trace(scene,'scatter3d',{meta:{layer:'measured'},mode:'markers',x:rows.map(r=>r.xy[0]),y:rows.map(r=>r.xy[1]),
      z:rows.map(()=>-mid),customdata:rows.map(r=>[r.trace_id,r.position_status,r.quality_flags,r.time_utc]),
      marker:{size:3,color:rows.map(r=>r.value),cmin:0,cmax:data.rms_limit,colorscale:'Viridis',
        showscale:scene==='scene'&&!$('interpolated').checked&&!$('coverage').checked,
        colorbar:{title:{text:'RMS<br>'+data.attribute_unit},thickness:14,len:.65}},
      hovertemplate:'%{customdata[0]}<br>RMS %{marker.color:.2f} '+data.attribute_unit+
        '<br>Position: %{customdata[1]} · flags %{customdata[2]}<br>UTC %{customdata[3]}<extra></extra>'}));
  }
  // One WebGL object per compatible layer. Null columns keep every original gap.
  const combined=[], groups=new Map();
  for(const item of out){
    const kind=item.meta?.layer;
    const key=kind==='tracks'?'tracks':kind==='curtain'?JSON.stringify(item.meta.vertical):null;
    if(key===null){combined.push(item);continue;}
    if(!groups.has(key)){groups.set(key,item);combined.push(item);continue;}
    const target=groups.get(key);
    if(kind==='tracks')for(const field of ['x','y','z','customdata'])
      target[field]=target[field].concat([null],item[field]);
    else for(const field of ['x','y','z','surfacecolor','customdata'])
      target[field]=target[field].map((row,j)=>row.concat([null],item[field][j]));
  }
  return combined;
}
function sceneLayout(s,domain){const ns=data.gate_unit==='ns';
  const end=Math.max(data.gates.at(-1)[1],...s.curtains.map(c=>c.vertical.at(-1)));
  const xy=visibleBoundary(s), xs=xy.map(p=>p[0]),ys=xy.map(p=>p[1]);
  const w=Math.max(...xs)-Math.min(...xs),h=Math.max(...ys)-Math.min(...ys),scale=Math.max(w,h,1);
  const factor=Number($('exaggeration').value)||1;
  return {domain,camera,uirevision:cameraRevision,aspectmode:'manual',aspectratio:{x:w/scale,y:Math.max(h/scale,.1),
    z:(ns?.65:end/scale)*factor},bgcolor:'#0c141d',
    xaxis:{title:{text:s.coordinate_mode==='raw_gps_relative'?'Approx. east offset (m)':
      s.diagnostic?'Along-line distance (m)':'Local east (m)'},gridcolor:'#283b4c'},
    yaxis:{title:{text:s.coordinate_mode==='raw_gps_relative'?'Approx. north offset (m)':
      s.diagnostic?'Schematic cross-line (m)':'Local north (m)'},gridcolor:'#283b4c'},
    zaxis:{title:{text:ns?(data.vertical_axis==='recording_time'?'Recording time (ns)':'Corrected time (ns)'):
      'Calibrated depth (m)'},range:[-end,0],tickvals:[-end,-end/2,0],
      ticktext:[end.toFixed(1),(end/2).toFixed(1),'0'],gridcolor:'#283b4c'}};
}
async function render(){
  desired++;if(busy)return;busy=true;
  while(rendered<desired){const revision=desired,t0=performance.now();
    const gate=Number($('slice').value), selected=Number($('plotSelect').value);
    const overview=$('mode').value==='overview', chosen=overview?data.scenes:[data.scenes[selected]];
    const traces=[],layout={paper_bgcolor:'#0c141d',plot_bgcolor:'#0c141d',font:{color:'#c6d5e2',size:11},
      margin:{l:overview?55:5,r:95,t:35,b:75},annotations:[],uirevision:'view'};
    chosen.forEach((s,i)=>{const name=i?'scene'+(i+1):'scene';
      let domain={x:[0,1],y:[0,1]};if(overview){const col=i%2,row=Math.floor(i/2);
        domain={x:[col*.53,col*.53+.43],y:[row?0:.62,row?.4:1]};}
      if(overview){
        // Shared-scale plan maps avoid four simultaneous WebGL scene redraws.
        const suffix=i?String(i+1):'', x='x'+suffix,y='y'+suffix;
        layout['xaxis'+suffix]={domain:domain.x,anchor:y,title:{text:'Local east (m)',font:{size:10}},
          range:[Math.min(...s.boundary.map(p=>p[0]))-2,Math.max(...s.boundary.map(p=>p[0]))+2],
          constrain:'domain',gridcolor:'#283b4c',zeroline:false,nticks:4};
        layout['yaxis'+suffix]={domain:domain.y,anchor:x,scaleanchor:x,scaleratio:1,
          title:{text:'Local north (m)',font:{size:10}},
          range:[Math.min(...s.boundary.map(p=>p[1]))-2,Math.max(...s.boundary.map(p=>p[1]))+2],
          constrain:'domain',gridcolor:'#283b4c',zeroline:false,nticks:4};
        traces.push(...layers(s,name,gate,true).map(t=>{
          const map={...t,type:t.type==='surface'?'heatmap':'scatter',xaxis:x,yaxis:y};
          delete map.scene;delete map.z;
          if(t.type==='surface'){
            map.z=t.surfacecolor;map.zmin=t.cmin;map.zmax=t.cmax;map.hoverongaps=false;
            map.hovertemplate=t.hovertemplate.replaceAll('%{surfacecolor','%{z');
            for(const field of ['surfacecolor','cmin','cmax'])delete map[field];
          }return map;
        }));
      }else{layout[name]=sceneLayout(s,domain);traces.push(...layers(s,name,gate,false));}
      if(overview)layout.annotations.push({text:s.plot_id,x:domain.x[0]+.22,y:domain.y[1],
        xref:'paper',yref:'paper',yshift:18,showarrow:false,font:{color:'#75e3c2'}});
    });
    const s=data.scenes[selected], g=s.grid;
    const selectedLines=lineState(s).selected,allLines=sceneLineIds(s);
    $('interval').textContent=(data.vertical_axis==='depth'?'Depth':
      data.vertical_axis==='corrected_time'?'Corrected time':'Recording time')+' · '+
      data.gates[gate].join(' – ')+' '+data.gate_unit;
    $('metric').textContent='Quantitative RMS · '+data.attribute_unit;
    ['top','oblique','side','reset','exaggeration'].forEach(id=>$(id).disabled=overview);
    $('status').textContent=(overview?'Plan overview · independent local coordinates · shared scale. Select a plot for 3D rotation. ':'')+
      selectedLines.size+' of '+allLines.length+' lines visible. '+
      (s.coordinate_mode==='raw_gps_relative'?
        'Approximate layout from raw NMEA coordinates; datum/CRS unverified; no geographic grid. ':
        s.diagnostic?'Diagnostic odometer coordinates; no geographic grid. ':
        s.crs+' · world origin '+s.origin_xy_m.join(', ')+' m. ')+
      'Ground plane assumed flat at 0 · vertical display ×'+$('exaggeration').value+
      (data.gate_unit==='ns'?' (time aspect is schematic). ':'. ')+
      s.quality.excluded_positions+' positions excluded · '+s.quality.excluded_windows+' signal windows excluded. '+
      (g?'Supported slice '+(100*g.coverage[gate]).toFixed(1)+'% · '+data.method+'. ':'')+
      'GPS position filtering changes measured layers only; grids retain saved support.';
    $('empty').textContent=!selectedLines.size?'No lines selected. Choose a partition or individual lines.':
      !g?(s.coordinate_mode==='raw_gps_relative'?
      'Recorded GPS line layout; gridding requires reviewed datum, CRS and plot geometry.':
      'Measured line review; gridding requires reviewed plot geometry.'):
      !g.coverage[gate]?'No supported surface in this gate. Measured tracks and curtains remain available.':'';
    const viewSignature=JSON.stringify([selected,overview,$('samples').value,
      $('exaggeration').value,...[...selectedLines].sort(),...toggles.map(id=>$(id).checked)]);
    try{
      if(initialized && lastViewSignature===viewSignature && lastGate!==gate){
        const surfaceIds=traces.flatMap((t,i)=>t.meta?.layer==='slice'?[i]:[]);
        if(surfaceIds.length)await Plotly.restyle($('plot'),{
          ...(!overview?{surfacecolor:surfaceIds.map(i=>traces[i].surfacecolor)}:{}),
          z:surfaceIds.map(i=>traces[i].z),customdata:surfaceIds.map(i=>traces[i].customdata),
          hovertemplate:surfaceIds.map(i=>traces[i].hovertemplate)},surfaceIds);
        const pointIds=traces.flatMap((t,i)=>t.meta?.layer==='measured'?[i]:[]);
        if(pointIds.length)await Plotly.restyle($('plot'),{
          x:pointIds.map(i=>traces[i].x),y:pointIds.map(i=>traces[i].y),
          ...(!overview?{z:pointIds.map(i=>traces[i].z)}:{}),customdata:pointIds.map(i=>traces[i].customdata),
          'marker.color':pointIds.map(i=>traces[i].marker.color)},pointIds);
      }else{
        await Plotly.react($('plot'),traces,layout,{responsive:true,displaylogo:false,plotGlPixelRatio:1,
          modeBarButtonsToRemove:['sendChartToCloud'],
          toImageButtonOptions:{filename:'soil-sensing-view',format:'png',scale:2}});
      }
      lastViewSignature=viewSignature;lastGate=gate;
      if(!initialized){$('plot').on('plotly_relayout',e=>{
        if(e['scene.camera'])camera=e['scene.camera'];});initialized=true;}
      const elapsed=performance.now()-t0;metrics.renders.push({gate,overview,ms:elapsed,trace_count:traces.length});
      if(metrics.startup_ms===null)metrics.startup_ms=performance.now();
      $('timing').textContent='Render '+Math.round(elapsed)+' ms';
      $('timing').dataset.benchmark=JSON.stringify({startup_ms:metrics.startup_ms,
        renders:metrics.renders.slice(-50),
        browser:typeof navigator==='undefined'?'unavailable':navigator.userAgent});
    }catch(error){$('empty').textContent='Rendering failed: '+error.message;}
    rendered=revision;
  }busy=false;
}
$('plotSelect').addEventListener('change',()=>{populateLineControls(Number($('plotSelect').value));render()});
$('lineSubset').addEventListener('change',()=>{
  applyLinePreset(data.scenes[Number($('plotSelect').value)],$('lineSubset').value);render();
});
$('lineAll').onclick=()=>{const s=data.scenes[Number($('plotSelect').value)];applyLinePreset(s,'all');render()};
$('lineNone').onclick=()=>{
  const s=data.scenes[Number($('plotSelect').value)],state=lineState(s);
  state.selected=new Set();state.preset='custom';populateLineControls(data.scenes.indexOf(s));render();
};
['mode','samples','exaggeration',...toggles].forEach(id=>$(id).addEventListener('change',render));
$('slice').addEventListener('input',render);
$('play').onclick=()=>{if(timer){clearInterval(timer);timer=null;$('play').textContent='Play slices';return;}
  $('play').textContent='Pause';timer=setInterval(()=>{if(!busy){$('slice').value=(Number($('slice').value)+1)%data.gates.length;render();}},700);};
['top','oblique','side','reset'].forEach(id=>$(id).onclick=()=>{camera=cameras[id==='reset'?'oblique':id];cameraRevision++;render()});
$('save').onclick=()=>{const state={schema_version:'0.1',scene_id:data.scene_id,camera,
  plot:Number($('plotSelect').value),mode:$('mode').value,gate:Number($('slice').value),
  lines:[...lineState(data.scenes[Number($('plotSelect').value)]).selected],
  position_support:$('samples').value,vertical_factor:Number($('exaggeration').value),
  layers:Object.fromEntries(toggles.map(id=>[id,$(id).checked])),
  scales:{amplitude_limit:data.amplitude_limit,rms_limit:data.rms_limit},
  vertical_axis:data.vertical_axis,gate_unit:data.gate_unit};
  const url=URL.createObjectURL(new Blob([JSON.stringify(state,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='view.json';a.click();URL.revokeObjectURL(url);};
$('restore').onchange=async e=>{try{const s=JSON.parse(await e.target.files[0].text());
  if(s.schema_version!=='0.1'||s.scene_id!==data.scene_id||!Number.isInteger(s.plot)||
    s.plot<0||s.plot>=data.scenes.length||!Number.isInteger(s.gate)||s.gate<0||s.gate>=data.gates.length||
    !['single','overview'].includes(s.mode)||!['all','measured'].includes(s.position_support)||
    !(s.vertical_factor>=.1&&s.vertical_factor<=5)||!s.camera?.eye)throw Error('Incompatible view file');
  if(s.mode==='overview'&&$('mode').options[1].disabled)throw Error('Overview unavailable for diagnostic lines');
  const available=new Set(sceneLineIds(data.scenes[s.plot]));
  if(s.lines!==undefined&&(!Array.isArray(s.lines)||s.lines.some(line=>!available.has(line))))
    throw Error('View file references unavailable lines');
  camera=s.camera;cameraRevision++;$('plotSelect').value=s.plot;$('slice').value=s.gate;$('mode').value=s.mode;
  $('samples').value=s.position_support;$('exaggeration').value=s.vertical_factor;
  if(s.lines!==undefined)lineStates.set(s.plot,{selected:new Set(s.lines),preset:'custom'});
  populateLineControls(s.plot);
  toggles.forEach(id=>$(id).checked=!!s.layers[id]);render();
}catch(error){$('empty').textContent=error.message;}};
populateLineControls(0);
render();
