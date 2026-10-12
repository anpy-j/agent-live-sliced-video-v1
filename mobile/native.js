import { Capacitor } from '@capacitor/core';
import { Filesystem, Directory } from '@capacitor/filesystem';
import { FileTransfer } from '@capacitor/file-transfer';
import { Share } from '@capacitor/share';

window.livecutNative = {
  isNative: () => Capacitor.isNativePlatform(),
  async download(url, name) {
    const destination = await Filesystem.getUri({directory: Directory.Cache, path: name});
    await FileTransfer.downloadFile({url, path: destination.uri});
    return destination.uri;
  },
  share: path => Share.share({title: '保存 LiveCut 成片', files: [path]})
};
