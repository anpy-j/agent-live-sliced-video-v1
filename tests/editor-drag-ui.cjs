const assert=require('node:assert/strict');
module.exports=async function verifyCrossTrackDrag(page){
  const sourceId=await page.locator('.edit-clip').first().evaluate(el=>el.parentElement.dataset.track);
  await page.locator('#newTrackKind').selectOption('video');await page.locator('#addTrack').click();
  const targetId=await page.locator('.editor-lane.video').last().getAttribute('data-track');
  const source=page.locator(`.editor-lane[data-track="${sourceId}"]`),target=page.locator(`.editor-lane[data-track="${targetId}"]`);
  async function dragTo(from,to){
    await page.locator('#timelineRows').scrollIntoViewIfNeeded();
    const clip=await from.locator('.edit-clip').boundingBox(),lane=await to.boundingBox();
    const hit=await page.evaluate(({x,y,startY})=>({end:document.elementFromPoint(x,y)?.closest('[data-track]')?.dataset.track,start:document.elementFromPoint(x,startY)?.closest('[data-clip]')?.dataset.clip,height:innerHeight}),{x:clip.x+clip.width/2,y:lane.y+25,startY:clip.y+15});
    assert.ok(hit.start&&hit.end===await to.getAttribute('data-track'),JSON.stringify({clip,lane,hit}));
    await page.mouse.move(clip.x+clip.width/2,clip.y+15);await page.mouse.down();
    await page.mouse.move(clip.x+clip.width/2,lane.y+25,{steps:8});await page.mouse.up();
  }
  await dragTo(source,target);assert.equal(await source.locator('.edit-clip').count(),0);assert.equal(await target.locator('.edit-clip').count(),1);
  await page.locator('[data-action="undo"]').click();assert.equal(await source.locator('.edit-clip').count(),1);
  await page.locator('[data-action="redo"]').click();assert.equal(await target.locator('.edit-clip').count(),1);
  const lock=page.locator(`button[data-track="${sourceId}"][data-toggle="locked"]`);
  await lock.click();await dragTo(target,source);assert.equal(await target.locator('.edit-clip').count(),1);assert.equal(await source.locator('.edit-clip').count(),0);await lock.click();
  await dragTo(target,page.locator('.editor-lane.audio').first());assert.equal(await target.locator('.edit-clip').count(),1);
  await page.locator('#saveEdit').click();await page.waitForFunction(()=>document.querySelector('#editorSaveState').textContent==='已保存');
  await page.reload();await target.locator('.edit-clip').waitFor();assert.equal(await source.locator('.edit-clip').count(),0);
  await target.locator('.edit-clip').click();
};
