'use strict';
const data = JSON.parse(document.getElementById('scene-data').textContent);
const $ = id => document.getElementById(id);
const toggles = ['boundary','tracks','curtains','measured','interpolated','coverage'];
const cameras = {
  oblique:{eye:{x:1.55,y:-1.75,z:1.2}}, top:{eye:{x:0,y:0,z:2.6},up:{x:0,y:1,z:0}},
  side:{eye:{x:0,y:-2.7,z:.1},up:{x:0,y:0,z:1}}
};
let camera = cameras.oblique, timer = null, busy = false;
let desired = 0, rendered = -1, initialized = false;
const metrics = {renders:[], startup_ms:null};
window.explorerMetrics = metrics;
data.scenes.forEach((s,i)=>{let o=document.createElement('option');o.value=i;
o.textContent=s.plot_id+' / '+(s.survey_id||'session unknown');$('plotSelect').append(o)});
$('slice').max=data.gates.length-1;
$('slice').value=data.view_defaults.gate;
$('badge').textContent=data.synthetic?'SYNTHETIC DATA · DEMONSTRATION':
  data.scenes.every(s=>s.diagnostic)?'DIAGNOSTIC LINE REVIEW':'REVIEWED PLOT GEOMETRY';
if(data.scenes.some(s=>s.diagnostic))$('mode').options[1].disabled=true;
function trace(scene,type,extra){return Object.assign({type,scene,showlegend:false},extra)}
function layers(s, scene, gate, overview){
  const out=[], mid=(data.gates[gate][0]+data.gates[gate][1])/2;
  let amplitudeScaleShown=false;
  const hover='East %{x:.1f} m · North %{y:.1f} m<extra></extra>';
  if($('boundary').checked){
    const b=[...s.boundary,s.boundary[0]];
    out.push(trace(scene,'scatter3d',{mode:'lines',x:b.map(p=>p[0]),y:b.map(p=>p[1]),z:b.map(()=>0),
      line:{color:'#8295a7',width:3},hovertemplate:hover}));
    // Explicit 20 m scale bar in local metric coordinates.
    const a=s.boundary[0];out.push(trace(scene,'scatter3d',{mode:'lines+text',
      x:[a[0],a[0]+s.scale_bar_m],y:[a[1],a[1]],z:[0,0],text:['',s.scale_bar_m+' m'],
      textposition:'top center',line:{color:'#e8f1f7',width:5},hoverinfo:'skip'}));
  }
  if(!overview){for(const c of s.curtains){
    const allowed=c.position_status.map(v=>$('samples').value==='all'||v==='measured'||s.diagnostic);
    const x=c.xy.map((p,i)=>allowed[i]?p[0]:null), y=c.xy.map((p,i)=>allowed[i]?p[1]:null);
    if($('tracks').checked)out.push(trace(scene,'scatter3d',{mode:'lines',x,y,z:x.map(()=>0),
      connectgaps:false,line:{color:'#75e3c2',width:3},customdata:c.trace_ids,
      hovertemplate:'%{customdata}<br>'+hover}));
    if($('curtains').checked && x.length>1){out.push(trace(scene,'surface',{
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
    out.push(trace(scene,'surface',{x:grid.x,y:grid.y,
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
  if($('measured').checked){const rows=s.observations[gate].filter(r=>
    $('samples').value==='all'||r.position_status==='measured'||s.diagnostic);
    out.push(trace(scene,'scatter3d',{mode:'markers',x:rows.map(r=>r.xy[0]),y:rows.map(r=>r.xy[1]),
      z:rows.map(()=>-mid),customdata:rows.map(r=>[r.trace_id,r.position_status,r.quality_flags,r.time_utc]),
      marker:{size:3,color:rows.map(r=>r.value),cmin:0,cmax:data.rms_limit,colorscale:'Viridis',
        showscale:scene==='scene'&&!$('interpolated').checked&&!$('coverage').checked,
        colorbar:{title:{text:'RMS<br>'+data.attribute_unit},thickness:14,len:.65}},
      hovertemplate:'%{customdata[0]}<br>RMS %{marker.color:.2f} '+data.attribute_unit+
        '<br>Position: %{customdata[1]} · flags %{customdata[2]}<br>UTC %{customdata[3]}<extra></extra>'}));
  }
  return out;
}
function sceneLayout(s,domain){const ns=data.gate_unit==='ns';
  const end=Math.max(data.gates.at(-1)[1],...s.curtains.map(c=>c.vertical.at(-1)));
  const xy=s.boundary, xs=xy.map(p=>p[0]),ys=xy.map(p=>p[1]);
  const w=Math.max(...xs)-Math.min(...xs),h=Math.max(...ys)-Math.min(...ys),scale=Math.max(w,h,1);
  const factor=Number($('exaggeration').value)||1;
  return {domain,camera,aspectmode:'manual',aspectratio:{x:w/scale,y:Math.max(h/scale,.1),
    z:(ns?.65:end/scale)*factor},bgcolor:'#0c141d',
    xaxis:{title:{text:s.diagnostic?'Along-line distance (m)':'Local east (m)'},gridcolor:'#283b4c'},
    yaxis:{title:{text:s.diagnostic?'Schematic cross-line (m)':'Local north (m)'},gridcolor:'#283b4c'},
    zaxis:{title:{text:ns?(data.vertical_axis==='recording_time'?'Recording time (ns)':'Corrected time (ns)'):
      'Calibrated depth (m)'},range:[-end,0],tickvals:[-end,-end/2,0],
      ticktext:[end.toFixed(1),(end/2).toFixed(1),'0'],gridcolor:'#283b4c'}};
}
async function render(){
  desired++;if(busy)return;busy=true;
  while(rendered<desired){const revision=desired,t0=performance.now();
    const gate=Number($('slice').value), selected=Number($('plotSelect').value);
    const overview=$('mode').value==='overview', chosen=overview?data.scenes:[data.scenes[selected]];
    const traces=[],layout={paper_bgcolor:'#0c141d',font:{color:'#c6d5e2',size:11},
      margin:{l:5,r:95,t:30,b:75},annotations:[],uirevision:'view'};
    chosen.forEach((s,i)=>{const name=i?'scene'+(i+1):'scene';
      let domain={x:[0,1],y:[0,1]};if(overview){const col=i%2,row=Math.floor(i/2);
        domain={x:[col*.52,col*.52+.47],y:[row?0:.53,row?.47:1]};}
      layout[name]=sceneLayout(s,domain);traces.push(...layers(s,name,gate,overview));
      if(overview)layout.annotations.push({text:s.plot_id,x:domain.x[0]+.22,y:domain.y[1],
        xref:'paper',yref:'paper',showarrow:false,font:{color:'#75e3c2'}});
    });
    const s=data.scenes[selected], g=s.grid;
    $('interval').textContent=(data.vertical_axis==='depth'?'Depth':
      data.vertical_axis==='corrected_time'?'Corrected time':'Recording time')+' · '+
      data.gates[gate].join(' – ')+' '+data.gate_unit;
    $('metric').textContent='Quantitative RMS · '+data.attribute_unit;
    $('status').textContent=(overview?'Independent plot frames · shared scale. ':'')+
      (s.diagnostic?'Diagnostic odometer coordinates; no geographic grid. ':s.crs+' · world origin '+s.origin_xy_m.join(', ')+' m. ')+
      'Ground plane assumed flat at 0 · vertical display ×'+$('exaggeration').value+
      (data.gate_unit==='ns'?' (time aspect is schematic). ':'. ')+
      s.quality.excluded_positions+' positions excluded · '+s.quality.excluded_windows+' signal windows excluded. '+
      (g?'Supported slice '+(100*g.coverage[gate]).toFixed(1)+'% · '+data.method+'. ':'')+
      'GPS position filtering changes measured layers only; grids retain saved support.';
    $('empty').textContent=!g?'Measured line review; gridding requires reviewed plot geometry.':
      !g.coverage[gate]?'No supported surface in this gate. Measured tracks and curtains remain available.':'';
    try{await Plotly.react($('plot'),traces,layout,{responsive:true,displaylogo:false,
      toImageButtonOptions:{filename:'soil-sensing-view',format:'png',scale:2}});
      if(!initialized){$('plot').on('plotly_relayout',e=>{
        if(e['scene.camera'])camera=e['scene.camera'];});initialized=true;}
      const elapsed=performance.now()-t0;metrics.renders.push({gate,overview,ms:elapsed,trace_count:traces.length});
      if(metrics.startup_ms===null)metrics.startup_ms=performance.now();
      $('timing').textContent='Render '+Math.round(elapsed)+' ms';
    }catch(error){$('empty').textContent='Rendering failed: '+error.message;}
    rendered=revision;
  }busy=false;
}
['plotSelect','mode','samples','exaggeration',...toggles].forEach(id=>$(id).addEventListener('change',render));
$('slice').addEventListener('input',render);
$('play').onclick=()=>{if(timer){clearInterval(timer);timer=null;$('play').textContent='Play slices';return;}
  $('play').textContent='Pause';timer=setInterval(()=>{if(!busy){$('slice').value=(Number($('slice').value)+1)%data.gates.length;render();}},700);};
['top','oblique','side','reset'].forEach(id=>$(id).onclick=()=>{camera=cameras[id==='reset'?'oblique':id];render()});
$('save').onclick=()=>{const state={schema_version:'0.1',scene_id:data.scene_id,camera,
  plot:Number($('plotSelect').value),mode:$('mode').value,gate:Number($('slice').value),
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
  camera=s.camera;$('plotSelect').value=s.plot;$('slice').value=s.gate;$('mode').value=s.mode;
  $('samples').value=s.position_support;$('exaggeration').value=s.vertical_factor;
  toggles.forEach(id=>$(id).checked=!!s.layers[id]);render();
}catch(error){$('empty').textContent=error.message;}};
render();
