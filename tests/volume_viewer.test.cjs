const test = require('node:test');
const assert = require('node:assert/strict');
const {maskedMesh,sameSelection,validateView,defaultAttribute} = require('../src/soil_sensing/visualization/assets/volume.js');
const {voxelMesh,unpack,statistics} = require('../src/soil_sensing/visualization/assets/volume.js');

test('volume draws closed supported cells and removes internal faces',()=>{
  const data=Array.from({length:2},()=>Array.from({length:2},()=>[3,3,3]));
  const mesh=voxelMesh([0,1,2],[0,1],[0,1],data,[[true,true]],2);
  assert.equal(mesh.cellCount,2);
  assert.equal(mesh.i.length,20); // ten exterior quads, no shared internal face
  assert.equal(voxelMesh([0,1,2],[0,1],[0,1],data,[[true,true]],4).i.length,0);
  data[1][1][2]=NaN;
  assert.equal(voxelMesh([0,1,2],[0,1],[0,1],data,[[true,true]],2).cellCount,1);
  assert.equal(voxelMesh([0,1,2],[0,1],[0,1],data,[[false,true]],2).cellCount,0);
});

test('volume interval and null corners cannot bridge unsupported cells',()=>{
  const data=Array.from({length:3},()=>[[2,2],[2,2]]);
  assert.equal(voxelMesh([0,1],[0,1],[0,1,2],data,[[true]],0,1,2).cellCount,1);
  data[1][0][0]=null;
  assert.equal(voxelMesh([0,1],[0,1],[0,1,2],data,[[true]],0).cellCount,0);
});

test('lossless packed transport preserves NaNs and late samples set colour limits',()=>{
  const buffer=Buffer.alloc(32);[1,NaN,-2,1e9].forEach((v,i)=>buffer.writeDoubleLE(v,i*8));
  const array=unpack({encoding:'float64-le-base64',shape:[1,2,2],data:buffer.toString('base64')});
  assert.equal(array[0][1][1],1e9);assert(Number.isNaN(array[0][0][1]));
  assert.throws(()=>unpack({encoding:'float64-le-base64',shape:[9],data:buffer.toString('base64')}));
  const data=[[[null,null]],[[3,9]],[[2,7]]];
  assert.equal(statistics(data).best,1);assert.equal(statistics(data).count,4);
  assert.equal(defaultAttribute({rms:data,envelope:data,signed_amplitude:data}),'rms');
});

test('clipped envelope defaults to a supported attribute without fabricating data',()=>{
  const missing=[[[null,null],[null,null]]];
  const zero=[[[null,0],[null,null]]];
  assert.equal(defaultAttribute({envelope:missing,signed_amplitude:zero,rms:missing}),'signed_amplitude');
  assert.equal(defaultAttribute({envelope:zero,signed_amplitude:zero,rms:missing}),'envelope');
  assert.equal(defaultAttribute({envelope:missing,signed_amplitude:missing,rms:missing}),'envelope');
});

test('masked slice topology requires all four corners including last cells',()=>{
  const mesh=maskedMesh([0,1,2],[0,1,2],[[0,0,0],[0,0,0],[0,0,0]],[[1,null,1],[1,1,1],[1,1,1]]);
  assert.equal(mesh.i.length,4);
  for(const vertices of mesh.i.map((v,i)=>[v,mesh.j[i],mesh.k[i]])) assert(!vertices.includes(1));
  assert.equal(maskedMesh([0,1],[0,1],[[1,1],[1,null]],[[1,1],[1,1]]).i.length,0);
});
test('source identity and position policy invalidate derived selection',()=>{
  const ids=['["source-a","LINE00"]','["source-b","LINE00"]'];
  assert(sameSelection(ids,[...ids].reverse()));
  assert(!sameSelection(ids,[ids[0]]));
  assert(!sameSelection(ids,[ids[0],ids[0]]));
});
test('saved view rejects foreign hashes, stale lines, bad indices and cameras',()=>{
  const payload={viewer_hash:'cube',scenes:[{attributes:{envelope:[]},vertical:[0,1],x:[1,2],y:[1,2],selection:{lines:[['s','p']]}}]};
  const view={schema_version:'1.0',viewer_hash:'cube',partition:0,mode:'volume',attribute:'envelope',slice:1,xclip:0,yclip:1,lines:['["s","p"]'],position:'accepted',opacity:.5,threshold:0,camera:null};
  assert.equal(validateView(view,payload),view);
  for(const change of [{viewer_hash:'other'},{partition:-1},{slice:2},{opacity:NaN},{threshold:-1},{lines:['["other","p"]']},{camera:{eye:{x:1,y:1,z:1}}}]) assert.throws(()=>validateView({...view,...change},payload));
});
