const test=require('node:test'),assert=require('node:assert/strict');
const C=require('../web/editor-core.js');
test('split preserves source ranges at non-unit speed',()=>{
 const t={clips:[{id:'a',start:2,in:10,duration:4,speed:1.3}]};
 const right=C.split(t,'a',4,30);assert.equal(t.clips[0].duration,2);assert.equal(right.in,12.6);assert.equal(right.duration,2);
});
test('ripple delete trims crossing music and shifts subtitle',()=>{
 const video={clips:[{id:'a',start:2,duration:2,in:0}]},music={clips:[{id:'m',start:0,duration:8,in:3,speed:1}]},sub={clips:[{id:'s',start:5,duration:1}]};
 C.rippleDelete({tracks:[video,music,sub]},video,'a');assert.equal(video.clips.length,0);assert.equal(music.clips.length,2);assert.equal(music.clips[1].in,7);assert.equal(music.clips[1].start,2);assert.equal(sub.clips[0].start,3);
});
test('locked tracks prevent destructive ripple',()=>{
 const v={clips:[{id:'a',start:0,duration:2}]},locked={locked:true,clips:[{id:'s',start:3,duration:1}]};
 assert.throws(()=>C.rippleDelete({tracks:[v,locked]},v,'a'));assert.equal(v.clips.length,1);
});
test('timeline copy assigns independent IDs and data',()=>{
 const original={id:'a',name:'one',tracks:[{id:'t',clips:[{id:'c',start:0,duration:1}]}]},copy=C.duplicateTimeline(original);
 assert.notEqual(copy.id,original.id);assert.notEqual(copy.tracks[0].clips[0].id,'c');copy.tracks[0].clips[0].duration=5;assert.equal(original.tracks[0].clips[0].duration,1);
});
