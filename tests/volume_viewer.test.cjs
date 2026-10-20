const test = require('node:test');
const assert = require('node:assert/strict');
const {maskedMesh,sameSelection,validateView} = require('../src/soil_sensing/visualization/assets/volume.js');

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
