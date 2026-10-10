(function (global) {
  const id = () => global.crypto?.randomUUID?.() || `clip-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const clone = value => JSON.parse(JSON.stringify(value));
  const end = c => Number(c.start) + Number(c.duration);
  function split(track, clipId, time, fps = 30) {
    if (track.locked) throw new Error('轨道已锁定');
    const i = track.clips.findIndex(c => c.id === clipId), c = track.clips[i], cut = Math.round(time * fps) / fps;
    if (!c || cut <= c.start + .5 / fps || cut >= end(c) - .5 / fps) throw new Error('播放头需要位于片段内部');
    const right = {...clone(c), id:id(), start:cut, duration:end(c)-cut,
      in:Number(c.in||0)+(cut-c.start)*Number(c.speed||1), transition:'none', fade_in:0};
    track.clips.splice(i,1,{...clone(c),duration:cut-c.start,fade_out:0},right);return right;
  }
  function rippleDelete(timeline, track, clipId) {
    if(track.locked)throw new Error('轨道已锁定');
    const c=track.clips.find(c=>c.id===clipId);if(!c)return;
    const start=c.start,stop=end(c),delta=c.duration;
    if(timeline.tracks.some(t=>t.locked&&t.clips.some(x=>end(x)>start)))throw new Error('波纹删除会影响锁定轨道，请先解锁');
    for(const t of timeline.tracks){const next=[];for(const x of t.clips){
      if(x.start>=stop)next.push({...x,start:x.start-delta});
      else if(end(x)<=start)next.push(x);
      else{if(x.start<start)next.push({...clone(x),duration:start-x.start,fade_out:0});
        if(end(x)>stop)next.push({...clone(x),id:id(),start,in:Number(x.in||0)+(stop-x.start)*Number(x.speed||1),duration:end(x)-stop,transition:'none',fade_in:0});}
    }t.clips=next;}
  }
  function duplicateTimeline(timeline){const t=clone(timeline);t.id=id();t.name+=' 副本';for(const track of t.tracks){track.id=id();for(const c of track.clips)c.id=id();}return t;}
  function addClip(track,asset,start=0){
    if(track.locked)throw new Error('轨道已锁定');
    if(!{video:['video','image'],image:['image'],audio:['audio','video']}[track.kind]?.includes(asset.kind))throw new Error('素材不适合这个轨道');
    const c={id:id(),asset_id:asset.id,start:Math.max(0,start),in:0,duration:asset.kind==='image'?5:asset.duration,
      speed:1,volume:track.kind==='audio'?.2:1,opacity:1,x:0,y:0,scale:track.kind==='image'?.2:1,rotation:0,effect:'none',transition:'none'};
    track.clips.push(c);return c;
  }
  const api={id,clone,end,split,rippleDelete,duplicateTimeline,addClip};global.LiveCutEditCore=api;
  if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
