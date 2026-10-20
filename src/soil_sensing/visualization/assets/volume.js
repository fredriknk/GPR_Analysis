/* Viewing and analyst decisions only. Scientific reconstruction stays in Python. */
"use strict";
const VolumeUI = (() => {
  function defaultAttribute(attributes) {
    return ["rms","envelope","signed_amplitude"].find(name=>attributes[name]?.some(plane=>plane.some(row=>row.some(Number.isFinite)))) || "envelope";
  }
  function unpack(array) {
    if(!array.encoding) return array;
    const bytes=Uint8Array.from(atob(array.data),c=>c.charCodeAt(0));
    if(array.encoding!=="float64-le-base64" || bytes.length!==array.shape.reduce((a,b)=>a*b,8)) throw Error("Invalid packed volume array");
    const view=new DataView(bytes.buffer);let offset=0;
    const nest=level=>Array.from({length:array.shape[level]},()=>level===array.shape.length-1?view.getFloat64((offset++)*8,true):nest(level+1));
    return nest(0);
  }
  function statistics(data) {
    const values=[],counts=data.map(plane=>{let n=0;for(const row of plane)for(const v of row)if(Number.isFinite(v)){values.push(Math.abs(v));n++;}return n;});
    values.sort((a,b)=>a-b);
    const percentile=q=>values.length?values[Math.min(values.length-1,Math.floor(q*(values.length-1)))]:0;
    return {count:values.length,counts,limit:percentile(.98)||1,threshold:percentile(.65),best:counts.indexOf(Math.max(...counts))};
  }
  function voxelMesh(x,y,z,data,cells,threshold,lo=0,hi=z.length-1,labels=null,candidate=0) {
    // Occupied cells require eight finite native corners and a complete domain
    // footprint. Exterior faces are display closures, never interpreted boundaries.
    const nx=x.length-1,ny=y.length-1,nz=z.length-1;
    const occupied=new Uint8Array(Math.max(0,nx*ny*nz));
    const values=new Float64Array(occupied.length),index=(a,b,c)=>(c*ny+b)*nx+a;
    let count=0;
    for(let c=lo;c<hi;c++)for(let b=0;b<ny;b++)for(let a=0;a<nx;a++) {
      if(cells && !cells[b][a])continue;
      if(labels && (!labels[c][b][a] || (candidate && labels[c][b][a]!==candidate)))continue;
      const corners=[data[c][b][a],data[c][b][a+1],data[c][b+1][a],data[c][b+1][a+1],data[c+1][b][a],data[c+1][b][a+1],data[c+1][b+1][a],data[c+1][b+1][a+1]];
      if(!corners.every(Number.isFinite))continue;
      const magnitude=corners.reduce((s,v)=>s+Math.abs(v),0)/8;
      if(magnitude<threshold)continue;
      const i=index(a,b,c);occupied[i]=1;values[i]=corners.reduce((s,v)=>s+v,0)/8;count++;
    }
    const mesh={type:"mesh3d",x:[],y:[],z:[],i:[],j:[],k:[],intensity:[],flatshading:true,cellCount:count};
    const faces=[[-1,0,0,[0,4,6,2]],[1,0,0,[1,3,7,5]],[0,-1,0,[0,1,5,4]],[0,1,0,[2,6,7,3]],[0,0,-1,[0,2,3,1]],[0,0,1,[4,5,7,6]]];
    for(let c=lo;c<hi;c++)for(let b=0;b<ny;b++)for(let a=0;a<nx;a++) {
      const idx=index(a,b,c);if(!occupied[idx])continue;
      for(const [dx,dy,dz,vertices] of faces) {
        const aa=a+dx,bb=b+dy,cc=c+dz;
        if(aa>=0&&aa<nx&&bb>=0&&bb<ny&&cc>=lo&&cc<hi&&occupied[index(aa,bb,cc)])continue;
        const start=mesh.x.length;
        for(const v of vertices){mesh.x.push(x[a+(v&1)]);mesh.y.push(y[b+((v>>1)&1)]);mesh.z.push(z[c+((v>>2)&1)]);mesh.intensity.push(values[idx]);}
        mesh.i.push(start,start);mesh.j.push(start+1,start+2);mesh.k.push(start+2,start+3);
      }
    }
    return mesh;
  }
  function nearestSource(profiles, bounds) {
    const x=(bounds.x_m[0]+bounds.x_m[1])/2,y=(bounds.y_m[0]+bounds.y_m[1])/2;
    let best=Infinity,index=0;
    profiles.forEach((p,i)=>p.xy.forEach(q=>{if(!q.every(Number.isFinite))return;const d=Math.hypot(q[0]-x,q[1]-y);if(d<best){best=d;index=i;}}));
    return index;
  }
  function sameSelection(expected, actual) {
    return expected.length === actual.length && [...expected].sort().every((v, i) => v === [...actual].sort()[i]);
  }
  function maskedMesh(x, y, z, values, cells=null) {
    const mesh = {type:"mesh3d",x:[],y:[],z:[],i:[],j:[],k:[],intensity:[],flatshading:true};
    const ny = z.length, nx = ny ? z[0].length : 0;
    for(let row=0;row<ny;row++) for(let col=0;col<nx;col++) {
      mesh.x.push(Array.isArray(x[0]) ? x[row][col] : x[col]);
      mesh.y.push(Array.isArray(y[0]) ? y[row][col] : y[row]);
      mesh.z.push(z[row][col] === null ? 0 : z[row][col]);
      mesh.intensity.push(values[row][col] === null ? 0 : values[row][col]);
    }
    for(let row=0;row<ny-1;row++) for(let col=0;col<nx-1;col++) {
      if(cells && !cells[row][col]) continue;
      const corners=[[row,col],[row,col+1],[row+1,col],[row+1,col+1]];
      if(!corners.every(([r,c])=>Number.isFinite(z[r][c]) && Number.isFinite(values[r][c]) && Number.isFinite(mesh.x[r*nx+c]) && Number.isFinite(mesh.y[r*nx+c]))) continue;
      const a=row*nx+col,b=a+1,c=a+nx,d=c+1;
      mesh.i.push(a,b);mesh.j.push(b,d);mesh.k.push(c,c);
    }
    return mesh;
  }
  function validateView(v,payload) {
    const scene=payload.scenes[v.partition];
    if(v.schema_version!=="1.0" || v.viewer_hash!==payload.viewer_hash || !scene || !["volume","anomalies","slices","plan","curtains","layers","drains"].includes(v.mode) || !Object.hasOwn(scene.attributes,v.attribute)) throw Error("View belongs to a different volume or has invalid fields");
    for(const [key,max] of [["slice",scene.vertical.length],["xclip",scene.x.length],["yclip",scene.y.length]]) if(!Number.isInteger(v[key]) || v[key]<0 || v[key]>=max) throw Error("Invalid view slice");
    if(!["accepted","measured"].includes(v.position) || !Array.isArray(v.lines) || !v.lines.every(s=>scene.selection.lines.map(JSON.stringify).includes(s)) || new Set(v.lines).size!==v.lines.length || !Number.isFinite(v.opacity) || v.opacity<.1 || v.opacity>1 || !Number.isFinite(v.threshold) || v.threshold<0) throw Error("Invalid view controls");
    if(v.camera && !["eye","up","center"].every(key=>v.camera[key] && ["x","y","z"].every(axis=>Number.isFinite(v.camera[key][axis])))) throw Error("Invalid camera");
    if(v.top!==undefined && (!Number.isInteger(v.top)||!Number.isInteger(v.bottom)||v.top<0||v.bottom>=scene.vertical.length||v.top>=v.bottom))throw Error("Invalid volume interval");
    if(v.contrast!==undefined && (!Number.isFinite(v.contrast)||v.contrast<.25||v.contrast>10))throw Error("Invalid contrast");
    if(v.source!==undefined && (!Number.isInteger(v.source)||v.source<0||v.source>=scene.source_profiles.length))throw Error("Invalid source");
    if(v.mode==="anomalies" && !scene.anomalies)throw Error("Anomaly detection unavailable");
    if(v.anomaly!==undefined && (!Number.isInteger(v.anomaly)||v.anomaly<0||(v.anomaly && !scene.anomalies?.candidates.some(c=>c.candidate_id===v.anomaly))))throw Error("Invalid anomaly");
    return v;
  }
  return {nearestSource,sameSelection,maskedMesh,voxelMesh,validateView,defaultAttribute,unpack,statistics};
})();
if(typeof module!=="undefined") module.exports=VolumeUI;
if(typeof document!=="undefined") (()=>{
  const payload=JSON.parse(document.getElementById("volume-data").textContent), $=id=>document.getElementById(id);
  for(const s of payload.scenes){if(s.anomaly_labels)s.anomaly_labels=VolumeUI.unpack(s.anomaly_labels);for(const key in s.attributes)s.attributes[key]=VolumeUI.unpack(s.attributes[key]);for(const p of s.source_profiles)p.amplitude=VolumeUI.unpack(p.amplitude);s.directions=Object.fromEntries((s.metadata.acquisition_audit||[]).map(a=>[JSON.stringify(JSON.parse(a.line_id)),a.direction_group]));s.stats=Object.fromEntries(Object.entries(s.attributes).map(([key,v])=>[key,VolumeUI.statistics(v)]));}
  let decisions=[], camera=null, renderPromise=Promise.resolve(), openingMs=null, measurements=[], renderQueued=false;
  payload.scenes.forEach((s,i)=>$("partition").add(new Option(s.partition_id,i)));
  const scene=()=>payload.scenes[+$("partition").value];
  const selected=()=>Array.from($("lines").selectedOptions,o=>o.value);
  function fresh(){const s=scene();return VolumeUI.sameSelection(s.selection.lines.map(JSON.stringify),selected()) && $("position").value===s.selection.position_policy;}
  function download(value,name){const blob=new Blob([JSON.stringify(value,null,2)],{type:"application/json"}),a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
  function setup(){const s=scene();$("source").replaceChildren();$("lines").replaceChildren();s.source_profiles.forEach((p,i)=>{$("source").add(new Option(p.profile_id,i));const o=new Option(p.profile_id,JSON.stringify([p.source_id,p.profile_id]));o.selected=true;$("lines").add(o);});for(const [key,axis]of [["slice",s.vertical],["xclip",s.x],["yclip",s.y]]){$(key).max=axis.length-1;$(key).value=Math.floor(axis.length/2);}$("position").value=s.selection.position_policy;$("download").href=s.download;$("badge").textContent=s.metadata.synthetic?"SYNTHETIC DEMO":s.metadata.diagnostic?"LOCAL DIAGNOSTIC":"REVIEWED GEOMETRY";$("capabilities").textContent=Object.entries(s.capabilities).filter(([,v])=>!v.allowed).map(([k,v])=>k.replaceAll("_"," ")+": "+v.missing.join(", ")).join(" · ");decisions=[];$("pickCount").textContent="0 review decisions";camera=null;
    $("anomaly").replaceChildren(new Option("All coherent anomalies",0));
    for(const c of s.anomalies?.candidates||[])$("anomaly").add(new Option(`Candidate ${c.candidate_id} / ${c.cell_count} cells / peak ${c.peak_score.toFixed(1)}`,c.candidate_id));
    $("attribute").value=VolumeUI.defaultAttribute(s.attributes);
    for(const o of $("attribute").options){o.disabled=!s.stats[o.value].count;o.textContent={rms:"Local window RMS",envelope:"Hilbert envelope",signed_amplitude:"Signed amplitude"}[o.value]+(o.disabled?" — no valid samples":"");}
    for(const o of $("mode").options){o.disabled=o.value==="anomalies"?!s.anomalies:o.value==="layers"?!s.interpretations.some(v=>v.kind==="interpreted_layers"):o.value==="drains"?!s.interpretations.some(v=>v.kind==="drainage_candidates"):false;}
    if($("mode").selectedOptions[0].disabled)$("mode").value="volume";
    $("availability").textContent=`${s.source_profiles.length} lines in one grid · ${new Set((s.metadata.acquisition_audit||[]).map(a=>a.direction_group)).size} acquisition directions. ${s.stats.envelope.count?"":"Envelope unavailable: clipping affects the nonlocal Hilbert transform. "}${s.interpretations.length?"":"Layers and drainage need interpretation inputs."}`;
    $("top").max=$("bottom").max=s.vertical.length-1;$("top").value=0;$("bottom").value=s.vertical.length-1;
    $("contrast").value=1;
    setAttribute();
    $("layer").replaceChildren(new Option("All interpreted layers","all"));
    s.interpretations.filter(o=>o.kind==="interpreted_layers").forEach(o=>o.layers.forEach(l=>$("layer").add(new Option(l.layer_id,l.layer_id))));
    $("candidate").replaceChildren();
    s.interpretations.filter(o=>o.kind==="drainage_candidates").forEach(o=>o.candidates.forEach(c=>$("candidate").add(new Option(`${c.candidate_id} · ${c.status}`,c.candidate_id))));
    render();bscan();}
  function setAttribute(){const s=scene(),stats=s.stats[$("attribute").value];$("slice").value=Math.max(0,stats.best);$("threshold").value=Number(stats.threshold.toPrecision(4));$("threshold").step=Math.max(.01,stats.limit/100);}
  function controls(){const mode=$("mode").value;for(const [id,enabled]of [["anomaly",mode==="anomalies"],["slice",["slices","plan"].includes(mode)],["xclip",mode==="slices"],["yclip",mode==="slices"],["top",["volume","anomalies"].includes(mode)],["bottom",["volume","anomalies"].includes(mode)],["threshold",mode==="volume"],["layer",mode==="layers"],["opacity",!["plan"].includes(mode)],["attribute",!["curtains","drains","layers","anomalies"].includes(mode)]]){$(id).disabled=!enabled;$(id).hidden=!enabled;const label=document.querySelector(`label[for="${id}"]`);if(label)label.hidden=!enabled;}$("volumeRangeLabel").hidden=!["volume","anomalies"].includes(mode);$("intervalLabel").textContent=`${scene().vertical[+$("top").value].toFixed(1)}–${scene().vertical[+$("bottom").value].toFixed(1)} ${scene().metadata.vertical_unit}`;}
  function render(){if(renderQueued)return renderPromise;renderQueued=true;renderPromise=renderPromise.then(async()=>{renderQueued=false;
    controls();
    const t0=performance.now(), s=scene(), ok=true, attr=$("mode").value==="anomalies"?"rms":$("attribute").value, data=s.attributes[attr], zi=+$("slice").value,xi=+$("xclip").value,yi=+$("yclip").value,unit=s.metadata.vertical_unit;
    const threshold=+$("threshold").value, opacity=+$("opacity").value, signed=attr==="signed_amplitude"||$("mode").value==="curtains", stats=s.stats[attr];
    const max=stats.limit / +$("contrast").value, traces=[];
    const mesh=(x,y,z,v,name,cells=null)=>{const m=VolumeUI.maskedMesh(x,y,z,v,cells);Object.assign(m,{name,opacity,colorscale:signed?"RdBu":"Viridis",cmin:signed?-max:0,cmax:max,showscale:!traces.some(t=>t.showscale),colorbar:{title:{text:`${attr} (${s.metadata.attribute_unit})`}},hovertemplate:`%{x:.2f} m, %{y:.2f} m<br>%{z:.2f} ${unit}<br>%{intensity:.2f} ${s.metadata.attribute_unit}<extra>${name}</extra>`});traces.push(m);};
    const keep=v=>Number.isFinite(v)?v:null;
    const filtered=plane=>plane.map(row=>row.map(keep));
    let volumeCells=0;
    if(["volume","anomalies"].includes($("mode").value)){
      const m=VolumeUI.voxelMesh(s.x,s.y,s.vertical,data,s.cells,$("mode").value==="anomalies"?0:threshold,+$("top").value,+$("bottom").value,$("mode").value==="anomalies"?s.anomaly_labels:null,+$("anomaly").value);volumeCells=m.cellCount;delete m.cellCount;
      Object.assign(m,{name:"Supported radar cells",opacity,colorscale:signed?"RdBu":"Viridis",cmin:signed?-max:0,cmax:max,showscale:true,colorbar:{title:{text:`${attr} (${s.metadata.attribute_unit})`}},hovertemplate:`East %{x:.2f} m · north %{y:.2f} m<br>%{z:.2f} ${unit}<br>Cell mean %{intensity:.2f} ${s.metadata.attribute_unit}<extra>Display cell boundary</extra>`});if($("mode").value==="anomalies")m.cmax=VolumeUI.statistics([[m.intensity]]).limit / +$("contrast").value;traces.push(m);
    }
    if($("mode").value==="plan")traces.push({type:"heatmap",x:s.x,y:s.y,z:data[zi].map(row=>row.map(keep)),colorscale:signed?"RdBu":"Viridis",zmin:signed?-max:0,zmax:max,connectgaps:false,colorbar:{title:{text:`${attr} (${s.metadata.attribute_unit})`}},hovertemplate:`East %{x:.2f} m · north %{y:.2f} m<br>%{z:.2f} ${s.metadata.attribute_unit}<extra>Supported sample slice</extra>`});
    if(ok && $("mode").value==="slices"){
      mesh(s.x,s.y,s.y.map(()=>s.x.map(()=>s.vertical[zi])),filtered(data[zi]),"Horizontal supported slice",s.cells);
      mesh(s.vertical.map(()=>s.y.map(()=>s.x[xi])),s.vertical.map(()=>s.y),s.vertical.map(v=>s.y.map(()=>v)),data.map(p=>p.map(r=>keep(r[xi]))),"X cutaway",s.vertical.slice(1).map(()=>s.x_segments.map(r=>r[xi])));
      mesh(s.vertical.map(()=>s.x),s.vertical.map(()=>s.x.map(()=>s.y[yi])),s.vertical.map(v=>s.x.map(()=>v)),data.map(p=>p[yi].map(keep)),"Y cutaway",s.vertical.slice(1).map(()=>s.y_segments[yi]));
    }
    for(const p of s.source_profiles){if(!selected().includes(JSON.stringify([p.source_id,p.profile_id])))continue;let x=[],y=[],z=[];for(let i=0;i<p.xy.length;i++){const point=p.xy[i],prev=i?p.xy[i-1]:null;if($("position").value==="measured"&&p.position_status[i]!=="measured"){x.push(null);y.push(null);z.push(null);continue;}if(prev && point[0]!==null && prev[0]!==null && Math.hypot(point[0]-prev[0],point[1]-prev[1])>s.metadata.track_gap_m){x.push(null);y.push(null);z.push(null);}x.push(point[0]);y.push(point[1]);z.push(0);}traces.push({type:"scatter3d",mode:"lines",x,y,z,line:{color:p===s.source_profiles[+$("source").value]?"#ffffff":s.directions[JSON.stringify([p.source_id,p.profile_id])]==="north_south"?"#ffb66d":"#61dcbc",width:p===s.source_profiles[+$("source").value]?6:3},name:p.profile_id,showlegend:false});}
    const source=s.source_profiles[+$("source").value];
    if($("mode").value==="curtains" && source && selected().includes(JSON.stringify([source.source_id,source.profile_id]))){const xy=source.xy.map((v,i)=>$("position").value==="measured"&&source.position_status[i]!=="measured"?[null,null]:v);for(let i=1;i<xy.length;i++)if(xy[i][0]!==null&&xy[i-1][0]!==null&&Math.hypot(xy[i][0]-xy[i-1][0],xy[i][1]-xy[i-1][1])>s.metadata.track_gap_m)xy[i]=[null,null];const x=source.time.map(()=>xy.map(v=>v[0])),y=source.time.map(()=>xy.map(v=>v[1])),z=source.time.map(t=>source.xy.map(()=>t)),v=source.time.map((_,j)=>source.amplitude.map(row=>row[j]));mesh(x,y,z,v,"Native signed amplitude / recording ns");const last=traces[traces.length-1],limit=(source.displayLimit||VolumeUI.statistics([source.amplitude]).limit)/ +$("contrast").value;last.cmin=-limit;last.cmax=limit;last.colorbar.title.text=`Signed amplitude (${s.metadata.attribute_unit})`;}
    let objectCount=0;
    if(ok && $("mode").value==="layers") for(const obj of s.interpretations.filter(o=>o.kind==="interpreted_layers")) {
      for(const shell of obj.shells||[]) {
        if($("layer").value!=="all" && $("layer").value!==shell.object_id) continue;
        const v=shell.vertices,f=shell.faces;
        traces.push({type:"mesh3d",x:v.map(v=>v[0]),y:v.map(v=>v[1]),z:v.map(v=>v[2]),i:f.map(v=>v[0]),j:f.map(v=>v[1]),k:f.map(v=>v[2]),intensity:shell.intensity,colorscale:"Viridis",opacity,showscale:true,colorbar:{title:{text:`${shell.object_id} (${obj.layer_unit})`}},name:`${shell.object_id} · ${obj.review_status}`,hovertemplate:`%{intensity:.2f} ${obj.layer_unit}<br>Supported vertical interval; caps are display closures<extra>${shell.object_id}</extra>`});
        objectCount++;
      }
      if(!obj.shells?.length) obj.values.forEach((values,i)=>{mesh(s.x,s.y,values,values,`${obj.horizons[i]} · ${obj.review_status}`);objectCount++;});
    }
    if(ok && $("mode").value==="drains")for(const obj of s.interpretations.filter(o=>o.kind==="drainage_candidates")){const detections=obj.detections.filter(d=>d.accepted_detection);traces.push({type:"scatter3d",mode:"markers",x:detections.map(d=>d.xy[0]),y:detections.map(d=>d.xy[1]),z:detections.map(d=>d.two_way_time_ns),marker:{size:5,color:"#ffbd72"},text:detections.map(d=>`${d.detection_id}<br>Evidence score ${d.score?.toFixed(3)}; proposed<br>Time ${d.two_way_time_ns} ns; radius symbolic`),hovertemplate:"%{text}<extra></extra>",name:"Candidate crossings (time ns)"});for(const e of obj.association_graph){const a=detections.find(d=>d.detection_id===e.left),b=detections.find(d=>d.detection_id===e.right);if(a&&b)traces.push({type:"scatter3d",mode:"lines",x:[a.xy[0],b.xy[0]],y:[a.xy[1],b.xy[1]],z:[a.two_way_time_ns,b.two_way_time_ns],line:{color:"#ffbd72",width:6},name:"Alternative symbolic path (symbolic width)",showlegend:false});}objectCount+=detections.length;}
    $("state").textContent=fresh()?"Cube uses all frozen grid contributors. Source track filters do not change the cube.":"Source tracks are filtered. Cube still uses all frozen contributors; save a reconstruction request to change the scientific selection.";
    $("sliceLabel").textContent=`${s.vertical[zi].toFixed(2)} ${unit}`;$("xLabel").textContent=`${s.x[xi].toFixed(1)} m`;$("yLabel").textContent=`${s.y[yi].toFixed(1)} m`;
    const triangleCount=traces.filter(t=>t.type==="mesh3d").reduce((n,t)=>n+t.i.length,0);
    $("notice").textContent=`${s.metadata.coordinate_note}. ${s.metadata.vertical_axis.replaceAll("_"," ")} (${unit}), positive down · flat relative ground · preview stride ${s.preview_step}. ${$("mode").value==="volume"?`Supported cell boundaries, threshold on mean absolute corner amplitude; closures are not soil layers. ${!volumeCells?"No complete cells at this threshold/interval: lower threshold, widen the interval, or inspect time slices and B-scans.":""}`:""} ${["slices","plan"].includes($("mode").value)&&!stats.counts[zi]?"No supported samples on this horizontal slice; move the time/depth slider or reset to the best-supported slice.":""} ${["layers","drains"].includes($("mode").value)&&!objectCount?"No supported interpretation in this selection.":""}`;
    if($("mode").value==="anomalies")$("notice").textContent+=` Colour scale follows visible anomaly cells. Coherent RMS anomalies: six-neighbour complete cells, slice-relative score >= ${s.anomalies.threshold}; minimum ${s.anomalies.minimum_cells} cells. Selecting a candidate opens the nearest source track to its horizontal centre for inspection; this does not establish independent confirmation. ${s.anomalies.limitation}`;
    const zLabel=$("mode").value==="curtains"||$("mode").value==="drains"?"Recording time (ns)":`${s.metadata.vertical_axis.replaceAll("_"," ")} (${unit})`;
    const layout={paper_bgcolor:"#0d141c",font:{color:"#d7e4ef"},margin:{l:0,r:70,t:10,b:0},showlegend:false,uirevision:s.partition_id,scene:{bgcolor:"#0d141c",xaxis:{title:{text:"Local east (m)"}},yaxis:{title:{text:"Local north (m)"}},zaxis:{title:{text:zLabel},autorange:"reversed"},aspectmode:"manual",aspectratio:{x:1,y:(s.y.at(-1)-s.y[0])/(s.x.at(-1)-s.x[0]),z:.65},camera:camera||{eye:{x:1.5,y:1.5,z:1.1}}}};
    if($("mode").value==="plan"){delete layout.scene;layout.xaxis={title:{text:"Local east (m)"}};layout.yaxis={title:{text:"Local north (m)"},scaleanchor:"x"};for(let i=traces.length-1;i>=0;i--)if(traces[i].type==="scatter3d"){traces[i].type="scatter";delete traces[i].z;}}
    await Plotly.react("scene",traces,layout,{responsive:true,displaylogo:false});
    if(!$("scene")._volumeCameraListener){$("scene").on("plotly_relayout",event=>{if(event["scene.camera"])camera=event["scene.camera"];});$("scene")._volumeCameraListener=true;}
    $("supportLabel").textContent=`${stats.count.toLocaleString()} supported samples (${(100*stats.count/(s.x.length*s.y.length*s.vertical.length)).toFixed(1)}% of box) · ${volumeCells.toLocaleString()} visible 3D cells · ${triangleCount.toLocaleString()} faces. Support is not uncertainty.`;
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    if(openingMs===null) openingMs=performance.now();
    const measurement={render_ms:performance.now()-t0,partition:s.partition_id,mode:$("mode").value,stale:!ok,objects:objectCount,triangles:triangleCount};
    measurements.push(measurement);
    document.body.dataset.volumeMetrics=JSON.stringify({opening_ms:openingMs,user_agent:navigator.userAgent,renders:measurements.slice(-100)});
  }).catch(e=>{renderQueued=false;$("state").textContent=e.message;});return renderPromise;}
  function bscan(){const p=scene().source_profiles[+$("source").value];if(!p)return;if(!p.displayLimit)p.displayLimit=VolumeUI.statistics([p.amplitude]).limit;const limit=p.displayLimit / +$("contrast").value, current=scene(), indices=["volume","anomalies"].includes($("mode").value)?[+$("top").value,+$("bottom").value]:[+$("slice").value], shapes=current.native_samples?indices.map(i=>({type:"line",xref:"paper",x0:0,x1:1,y0:p.time[current.native_samples[i]],y1:p.time[current.native_samples[i]],line:{color:"#ffbd72",width:1,dash:"dot"}})):[];Plotly.react("bscan",[{type:"heatmap",x:p.trace_ids.map((_,i)=>i),y:p.time,customdata:p.time.map(()=>p.trace_ids.map((t,i)=>`${p.source_id} / ${p.profile_id}<br>Trace ${t}; ${p.position_status[i]}; flags ${p.quality_flags[i]}`)),z:p.time.map((_,j)=>p.amplitude.map(r=>r[j])),colorscale:"RdBu",zmin:-limit,zmax:limit,colorbar:{title:{text:scene().metadata.attribute_unit}},hovertemplate:"%{customdata}<br>Native trace index %{x}<br>Recording %{y} ns<br>%{z}<extra></extra>"}],{paper_bgcolor:"#131e29",plot_bgcolor:"#131e29",font:{color:"#c8d9e6"},margin:{t:25,b:40,l:70,r:70},title:{text:p.profile_id,font:{size:12}},shapes,xaxis:{title:{text:"Native trace index"}},yaxis:{title:{text:"Recording time (ns)"},autorange:"reversed"}},{responsive:true,displaylogo:false}).then(()=>{$("bscan").removeAllListeners("plotly_click");$("bscan").on("plotly_click",event=>{const q=event.points[0],i=Math.round(q.x),j=p.time.indexOf(q.y);if(j<0)return;const pick={source_id:p.source_id,profile_id:p.profile_id,trace_id:p.trace_ids[i],horizon_id:$("horizon").value,native_sample:j,time_basis:"recording",two_way_time_ns:p.time[j],standard_uncertainty_ns:null,status:$("reviewStatus").value};decisions=decisions.filter(d=>!["source_id","profile_id","trace_id","horizon_id"].every(k=>d[k]===pick[k]));decisions.push(pick);$("pickCount").textContent=`${decisions.length} review decisions`;});});}
  $("anomaly").onchange=()=>{const c=scene().anomalies.candidates.find(c=>c.candidate_id===+$("anomaly").value);if(c){$("top").value=c.index_bounds[0][0];$("bottom").value=c.index_bounds[0][1];$("source").value=VolumeUI.nearestSource(scene().source_profiles,c.bounds);}else{$("top").value=0;$("bottom").value=scene().vertical.length-1;}render();bscan();};
  $("partition").onchange=setup;$("source").onchange=()=>{bscan();render();};for(const key of ["mode","layer","lines","position"])$(key).addEventListener("change",()=>{render();if(key==="mode")bscan();});
  for(const key of ["slice","xclip","yclip","opacity","threshold","contrast","top","bottom"])$(key).addEventListener("input",()=>{if(+$("top").value>=+$("bottom").value){if(key==="top")$("top").value=+$("bottom").value-1;else $("bottom").value=+$("top").value+1;}render();if(["contrast","slice","top","bottom"].includes(key))bscan();});
  $("attribute").onchange=()=>{setAttribute();render();bscan();};
  $("auto").onclick=()=>{setAttribute();$("contrast").value=1;render();bscan();};
  $("allLines").onclick=()=>{for(const o of $("lines").options)o.selected=true;$("position").value=scene().selection.position_policy;render();};
  $("reset").onclick=()=>{camera={eye:{x:1.5,y:1.5,z:1.1},up:{x:0,y:0,z:1},center:{x:0,y:0,z:0}};render();};
  $("image").onclick=()=>Plotly.downloadImage("scene",{format:"png",filename:scene().partition_id});
  $("request").onclick=()=>download({schema_version:"1.0",kind:"reconstruction_request",parent_selection_hash:scene().selection.selection_hash,partition_id:scene().partition_id,lines:selected().map(JSON.parse),position_policy:$("position").value},"reconstruction-request.json");
  $("decisions").onclick=()=>{if(!$("reviewer").value.trim()||!$("evidence").value.trim()){$("state").textContent="Reviewer and evidence are required to save decisions.";return;}if($("mode").value==="drains") {
      const obj=scene().interpretations.find(o=>o.kind==="drainage_candidates"&&o.candidates.some(c=>c.candidate_id===$("candidate").value));
      if(!obj){$("state").textContent="Select an existing drainage candidate.";return;}
      download({schema_version:"1.0",kind:"drainage_review",selection_hash:scene().selection.selection_hash,parent_hash:obj.interpretation_hash,decisions:[{candidate_id:$("candidate").value,status:$("reviewStatus").value,reviewer:$("reviewer").value.trim(),evidence:$("evidence").value.trim()}]},"drainage-review.json");return;
    }
    download({schema_version:"1.0",kind:"review_decisions",selection_hash:scene().selection.selection_hash,parent_hash:scene().interpretations.find(o=>o.interpretation)?.interpretation.interpretation_hash||null,decisions:decisions.map((d,i)=>({...d,pick_id:`review-${Date.now()}-${i}`,reviewer:$("reviewer").value.trim(),evidence:$("evidence").value.trim()}))},"review-decisions.json");};
  $("save").onclick=()=>download({schema_version:"1.0",viewer_hash:payload.viewer_hash,partition:+$("partition").value,mode:$("mode").value,attribute:$("attribute").value,slice:+$("slice").value,xclip:+$("xclip").value,yclip:+$("yclip").value,opacity:+$("opacity").value,threshold:+$("threshold").value,position:$("position").value,lines:selected(),top:+$("top").value,bottom:+$("bottom").value,contrast:+$("contrast").value,source:+$("source").value,anomaly:+$("anomaly").value,camera:camera},"volume-view.json");
  $("restore").onchange=async event=>{try{const v=VolumeUI.validateView(JSON.parse(await event.target.files[0].text()),payload);$("partition").value=v.partition;setup();for(const key of ["mode","attribute","slice","xclip","yclip","opacity","threshold","position","top","bottom","contrast","source","anomaly"])if(v[key]!==undefined)$(key).value=v[key];for(const o of $("lines").options)o.selected=v.lines.includes(o.value);camera=v.camera;bscan();render();}catch(e){$("state").textContent=e.message;}};

  setup();
})();
