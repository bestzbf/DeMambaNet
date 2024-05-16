import argparse
import builtins
import math
import os
import random
import shutil
import time
import warnings
import pickle
import numpy as np
from datetime import datetime
# import nibabel as nib

import torch
import torch.nn as nn
import torch.nn.parallel
import torch.nn.functional as F
import torch.backends.cudnn as cudnn
import torch.distributed as dist
import torch.optim
import torch.multiprocessing as mp
import torch.utils.data
import torch.utils.data.distributed
# import torchvision.transforms as transforms
# import torchvision.datasets as datasets
from torch.utils.tensorboard import SummaryWriter
from torch.optim.lr_scheduler import ReduceLROnPlateau
# import torchvision.models as models
# from loss_functions.metrics import dice_pytorch, SegmentationMetric

from loss_functions.dice_loss import SoftDiceLoss
import sys
sys.path.insert(0, sys.path[0]+"/../")
from models import sam_feat_seg_model_registry
from dataset import generate_dataset, generate_test_loader,generate_predict_loader
# from evaluate import test_synapse, test_acdc
from evaluate import test_two
from evaluate import test_IVUS109
from PIL import Image
from tqdm import tqdm


parser = argparse.ArgumentParser(description='PyTorch ImageNet Training')


parser.add_argument('-j', '--workers', default=32, type=int, metavar='N',
                    help='number of data loading workers (default: 32)')
parser.add_argument('--epochs', default=100, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
parser.add_argument('-b', '--batch-size', default=1, type=int,
                    metavar='N',
                    help='mini-batch size (default: 256), this is the total '
                         'batch size of all GPUs on the current node when '
                         'using Data Parallel or Distributed Data Parallel')
parser.add_argument('--lr', '--learning-rate', default=0.0005, type=float,
                    metavar='LR', help='initial learning rate', dest='lr')
parser.add_argument('--schedule', default=[120, 160], nargs='*', type=int,
                    help='learning rate schedule (when to drop lr by 10x)')
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum of SGD solver')
parser.add_argument('--wd', '--weight-decay', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)',
                    dest='weight_decay')
parser.add_argument('-p', '--print-freq', default=10, type=int,
                    metavar='N', help='print frequency (default: 10)')
parser.add_argument('--resume', default='', type=str, metavar='PATH',
                    help='path to latest checkpoint (default: none)')
parser.add_argument('--world-size', default=-1, type=int,
                    help='number of nodes for distributed training')
parser.add_argument('--rank', default=-1, type=int,
                    help='node rank for distributed training')
parser.add_argument('--dist-url', default='tcp://224.66.41.62:23456', type=str,
                    help='url used to set up distributed training')
parser.add_argument('--dist-backend', default='nccl', type=str,
                    help='distributed backend')
parser.add_argument('--seed', default=None, type=int,
                    help='seed for initializing training. ')
parser.add_argument('--gpu', default=0, type=int,
                    help='GPU id to use.')
parser.add_argument('--multiprocessing-distributed', action='store_true',
                    help='Use multi-processing distributed training to launch '
                         'N processes per node, which has N GPUs. This is the '
                         'fastest way to use PyTorch for either single node or '
                         'multi node data parallel training')

parser.add_argument('--model_type', type=str, default="vit_b", help='path to splits file')
parser.add_argument('--src_dir', type=str, default="F:\LmagesAndWords\AutoSAM-main", help='path to splits file')
parser.add_argument('--data_dir', type=str, default="F:\LmagesAndWords\AutoSAM-main\imgs", help='path to datafolder')
parser.add_argument('--model_dir', type=str, default="F:\LmagesAndWords\AutoSAM-main\imgs", help='path to moudel load')
parser.add_argument("--img_size", type=int, default=320)
parser.add_argument("--classes", type=int, default=8)
parser.add_argument("--do_contrast", default=False, action='store_true')
parser.add_argument("--slice_threshold", type=float, default=0.05)
parser.add_argument("--num_classes", type=int, default=14)
parser.add_argument("--fold", type=int, default=0)
parser.add_argument("--tr_size", type=int, default=1)
parser.add_argument("--save_dir", type=str, default=None)
parser.add_argument("--load_saved_model", action='store_true',
                        help='whether freeze encoder of the segmenter')
parser.add_argument("--saved_model_path", type=str, default=None)
parser.add_argument("--load_pseudo_label", default=False, action='store_true')
parser.add_argument("--dataset", type=str, default="ACDC")

def main():
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
        torch.manual_seed(args.seed)
        cudnn.deterministic = True
        warnings.warn('You have chosen to seed training. '
                      'This will turn on the CUDNN deterministic setting, '
                      'which can slow down your training considerably! '
                      'You may see unexpected behavior when restarting '
                      'from checkpoints.')

    if args.gpu is not None:
        warnings.warn('You have chosen a specific GPU. This will completely '
                      'disable data parallelism.')

    if args.dist_url == "env://" and args.world_size == -1:
        args.world_size = int(os.environ["WORLD_SIZE"])

    args.distributed = args.world_size > 1 or args.multiprocessing_distributed

    ngpus_per_node = torch.cuda.device_count()
    if args.multiprocessing_distributed:
        # Since we have ngpus_per_node processes per node, the total world_size
        # needs to be adjusted accordingly
        args.world_size = ngpus_per_node * args.world_size
        # Use torch.multiprocessing.spawn to launch distributed processes: the
        # main_worker process function
        mp.spawn(main_worker, nprocs=ngpus_per_node, args=(ngpus_per_node, args))
    else:
        # Simply call main_worker function
        main_worker(args.gpu, ngpus_per_node, args)


def main_worker(gpu, ngpus_per_node, args):
    args.gpu = gpu

    # suppress printing if not master
    if args.multiprocessing_distributed and args.gpu != 0:
        def print_pass(*args):
            pass
        builtins.print = print_pass

    if args.gpu is not None:
        print("Use GPU: {} for training".format(args.gpu))

    if args.distributed:
        if args.dist_url == "env://" and args.rank == -1:
            args.rank = int(os.environ["RANK"])
        if args.multiprocessing_distributed:
            # For multiprocessing distributed training, rank needs to be the
            # global rank among all the processes
            args.rank = args.rank * ngpus_per_node + gpu
        dist.init_process_group(backend=args.dist_backend, init_method=args.dist_url,
                                world_size=args.world_size, rank=args.rank)
    # create model

    if args.model_type=='vit_h':
        model_checkpoint = 'sam_vit_h_4b8939.pth'
    elif args.model_type == 'vit_l':
        model_checkpoint = 'sam_vit_l_0b3195.pth'
    elif args.model_type == 'vit_b':
        model_checkpoint = 'sam_vit_b_01ec64.pth'

    # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    # model = AttU_Net().cuda()
    # loaded_model = torch.load('weigh/Unet/1000/44.pth', map_location=device)
    # weights = loaded_model.state_dict()
    # model.load_state_dict(weights, strict=False)
    # model = U_Net().cuda()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = torch.load(args.model_dir, map_location=device)  # 加载.tar文件

    # model = torch.load(args.model_dir, map_location=device)
    # model = torch.load('weigh/autosam_skip+res/1000/39.pth', map_location=device)

    # model = sam_feat_seg_model_registry[args.model_type](num_classes=args.num_classes, checkpoint=model_checkpoint)


    optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=args.lr)
    scheduler = ReduceLROnPlateau(optimizer, 'min')

    # optionally resume from a checkpoint
    # if args.resume:
    #     if os.path.isfile(args.resume):
    #         print("=> loading checkpoint '{}'".format(args.resume))
    #         if args.gpu is None:
    #             checkpoint = torch.load(args.resume)
    #         else:
    #             # Map model to be loaded to specified single gpu.
    #             loc = 'cuda:{}'.format(args.gpu)
    #             checkpoint = torch.load(args.resume, map_location=loc)
    #         args.start_epoch = checkpoint['epoch']
    #         model.load_state_dict(checkpoint['state_dict'])
    #         optimizer.load_state_dict(checkpoint['optimizer'])
    #         print("=> loaded checkpoint '{}' (epoch {})"
    #               .format(args.resume, checkpoint['epoch']))
    #     else:
    #         print("=> no checkpoint found at '{}'".format(args.resume))

    cudnn.benchmark = True

    # Data loading code

    # train_loader, train_sampler, val_loader, val_sampler, test_loader, test_sampler = generate_dataset(args)
    # train_loader, val_loader,test_loader = generate_dataset(args)
    now = datetime.now()
    # args.save_dir = "output_experiment/Sam_h_seg_distributed_tr" + str(args.tr_size) # + str(now)[:-7]
    args.save_dir = "output_experiment/" + args.save_dir
    print(args.save_dir)
    if not os.path.exists(args.save_dir):
        os.makedirs(args.save_dir)





    # test_two(args)
    size = (512, 512)

    # predict(model, args, size)
    test(model, args)
    test_two(args)




    # if args.dataset == 'synapse':
    #     test_synapse(args)
    # elif args.dataset == 'ACDC' or args.dataset == 'acdc':
    #     test_acdc(args)




def predict(model, args, size):
    print('****************************************    predict    ****************************************')

    join = os.path.join

    if not os.path.exists(join(args.save_dir, "pre_mask")):
        os.mkdir(join(args.save_dir, "pre_mask"))
    # if not os.path.exists(join(args.save_dir, "mask")):
    #     os.mkdir(join(args.save_dir, "mask"))


    split_dir = os.path.join(args.src_dir, "splits.pkl")
    with open(split_dir, "rb") as f:
        splits = pickle.load(f)
    test_keys = splits[args.fold]['test']

    model.eval()

    preds = []
    labels = []

    files=[]
    for f in os.listdir(args.data_dir):
        if f in test_keys:
            files.append(f)
    data_loader = generate_predict_loader(test_keys, args, size)
    with torch.no_grad():
        for i, tup in enumerate(tqdm(data_loader)):
            if args.gpu is not None:
                img = tup[0].float().cuda(args.gpu, non_blocking=True)
                # label = tup[1].long().cuda(args.gpu, non_blocking=True)
            else:
                img = tup[0]
                # label = tup[1]
            img = img.unsqueeze(0)
            b, c, h, w = img.shape

            mask = model(img)
            mask = mask.view(b, -1, h, w)
            mask_softmax = F.softmax(mask, dim=1)
            mask = torch.argmax(mask_softmax, dim=1)


            # 二值化操作   bcun
            # threshold = 0.5  # 你可以根据需要设置这个阈值
            # mask = (mask > threshold).float()

            # ... 你的代码 ...

            pred_slice = np.asarray(mask.cpu().numpy())[0, :, :]
            # label_slice = np.asarray(label.cpu().numpy())[0, 0, :, :]

            # 将每个slice保存为png图像
            pred_slice[pred_slice == 1] = 255
            # pred_slice[pred_slice == 2] = 160
            # pred_slice[pred_slice==2] = 160


            pred_img = Image.fromarray(pred_slice.astype(np.uint8))

            pred_img.save(join(args.save_dir, 'pre_mask', files[i] ))

        # 保存测试的分割结果
    print("finish saving file:", test_keys)
    print("Finished test")



def test(model, args):
    print('****************************************    Test    ****************************************')

    join = os.path.join

    if not os.path.exists(join(args.save_dir, "pre_mask")):
        os.mkdir(join(args.save_dir, "pre_mask"))
    if not os.path.exists(join(args.save_dir, "mask")):
        os.mkdir(join(args.save_dir, "mask"))
    #
    # join = os.path.join
    #
    # save_dir = os.path.abspath(args.save_dir)
    # # os.mkdir(join(save_dir, "pre_mask"))
    #
    # if not os.path.exists(join(args.save_dir, "pre_mask")):
    #     os.mkdir(join(save_dir, "pre_mask"))
    # if not os.path.exists(join(args.save_dir, "mask")):
    #     os.mkdir(join(save_dir, "mask"))

    split_dir = os.path.join(args.src_dir, "splits.pkl")
    with open(split_dir, "rb") as f:
        splits = pickle.load(f)
    test_keys = splits[args.fold]['test']

    model.eval()

    preds = []
    labels = []

    files=[]
    for f in os.listdir(args.data_dir):
        if f in test_keys:
            files.append(f)
    data_loader = generate_test_loader(test_keys, args)

    with torch.no_grad():
        for i, tup in enumerate(tqdm(data_loader)):
            if args.gpu is not None:
                img = tup[0].float().cuda(args.gpu, non_blocking=True)
                label = tup[1].long().cuda(args.gpu, non_blocking=True)
            else:
                img = tup[0]
                label = tup[1]
            # print(img.shape)
            b, c, h, w = img.shape

            mask = model(img)
            mask = mask.view(b, -1, 320, 640)
            mask_softmax = F.softmax(mask, dim=1)
            mask = torch.argmax(mask_softmax, dim=1)

        #     preds.append(mask.cpu().numpy())
        #     labels.append(label.cpu().numpy())

        # preds = np.asarray(preds)
        # labels = np.asarray(labels)

        # print(preds.shape,labels.shape)

        # for i in range(preds.shape[0]): # 遍历每张2D slice
            # pred_slice = preds[i, 0, :, :]
            # label_slice = labels[i, 0, 0, :, :]

            pred_slice = np.asarray(mask.cpu().numpy())[0, :, :]
            label_slice = np.asarray(label.cpu().numpy())[0, 0, :, :]
            # 将每个slice保存为png图像
            pred_slice[pred_slice==1] = 255
            # pred_slice[pred_slice==2] = 160

            label_slice[label_slice==1] = 255
            # label_slice[label_slice==2] = 160
            # pred_slice[pred_slice == 1] = 255
            # pred_slice[pred_slice == 2] = 255
            #
            # label_slice[label_slice == 1] = 255
            # label_slice[label_slice == 2] = 255

            pred_img = Image.fromarray(pred_slice.astype(np.uint8))
            label_img = Image.fromarray(label_slice.astype(np.uint8))

            pred_img.save(join(args.save_dir, 'pre_mask', files[i] ))
            label_img.save(join(args.save_dir, 'mask',  files[i] ))

        # 保存测试的分割结果
    print("finish saving file:", test_keys)
    print("Finished test")



# def testold(model, args):
#     print('****************************************    Test    ****************************************')
#     join = os.path.join
#
#     if not os.path.exists(join(args.save_dir, "pre_mask")):
#         os.mkdir(join(args.save_dir, "pre_mask"))
#     if not os.path.exists(join(args.save_dir, "mask")):
#         os.mkdir(join(args.save_dir, "mask"))
#
#     split_dir = os.path.join(args.src_dir, "splits.pkl")
#     with open(split_dir, "rb") as f:
#         splits = pickle.load(f)
#     test_keys = splits[args.fold]['test']
#
#     model.eval()
#
#     preds = []
#     labels = []
#     data_loader = generate_test_loader(test_keys, args)
#     with torch.no_grad():
#         for i, tup in enumerate(data_loader):
#             if args.gpu is not None:
#                 img = tup[0].float().cuda(args.gpu, non_blocking=True)
#                 label = tup[1].long().cuda(args.gpu, non_blocking=True)
#             else:
#                 img = tup[0]
#                 label = tup[1]
#
#             b, c, h, w = img.shape
#
#             # mask, iou_pred = model(img)
#             mask = model(img)
#
#             mask = mask.view(b, -1, h, w)
#             mask_softmax = F.softmax(mask, dim=1)
#             mask = torch.argmax(mask_softmax, dim=1)
#
#             preds.append(mask.cpu().numpy())
#             labels.append(label.cpu().numpy())
#
#         preds = np.asarray(preds)
#         labels = np.asarray(labels)
#
#         print(preds.shape,labels.shape)
#
#         files=[]
#         for f in os.listdir(args.data_dir):
#             if f in test_keys:
#                 files.append(f)
#
#         for i in range(preds.shape[0]): # 遍历每张2D slice
#             pred_slice = preds[i, 0, :, :]
#             label_slice = labels[i, 0, 0, :, :]
#
#             # 将每个slice保存为png图像
#             pred_slice[pred_slice==1] = 80
#             pred_slice[pred_slice==2] = 160
#
#             label_slice[label_slice==1] = 80
#             label_slice[label_slice==2] = 160
#
#             pred_img = Image.fromarray(pred_slice.astype(np.uint8))
#             label_img = Image.fromarray(label_slice.astype(np.uint8))
#
#
#             pred_img.save(join(args.save_dir, 'pre_mask', files[i] ))
#             label_img.save(join(args.save_dir, 'mask',  files[i] ))
#
#         # 保存测试的分割结果
#     print("finish saving file:", test_keys)
#     print("Finished test")

#
# def save_checkpoint(state, is_best, filename='checkpoint.pth.tar'):
#     # torch.save(state, filename)
#     if is_best:
#         torch.save(state, filename)
#         shutil.copyfile(filename, 'model_best.pth.tar')


class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self, name, fmt=':f'):
        self.name = name
        self.fmt = fmt
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count

    def __str__(self):
        fmtstr = '{name} {val' + self.fmt + '} ({avg' + self.fmt + '})'
        return fmtstr.format(**self.__dict__)


class ProgressMeter(object):
    def __init__(self, num_batches, meters, prefix=""):
        self.batch_fmtstr = self._get_batch_fmtstr(num_batches)
        self.meters = meters
        self.prefix = prefix

    def display(self, batch):
        entries = [self.prefix + self.batch_fmtstr.format(batch)]
        entries += [str(meter) for meter in self.meters]
        print('\t'.join(entries))

    def _get_batch_fmtstr(self, num_batches):
        num_digits = len(str(num_batches // 1))
        fmt = '{:' + str(num_digits) + 'd}'
        return '[' + fmt + '/' + fmt.format(num_batches) + ']'


def adjust_learning_rate(optimizer, epoch, args):
    """Decay the learning rate based on schedule"""
    lr = args.lr
    if args.cos:  # cosine lr schedule
        lr *= 0.5 * (1. + math.cos(math.pi * epoch / args.epochs))
    else:  # stepwise lr schedule
        for milestone in args.schedule:
            lr *= 0.1 if epoch >= milestone else 1.
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


def accuracy(output, target, topk=(1,)):
    """Computes the accuracy over the k top predictions for the specified values of k"""
    with torch.no_grad():
        maxk = max(topk)
        batch_size = target.size(0)

        _, pred = output.topk(maxk, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target.view(1, -1).expand_as(pred))

        res = []
        for k in topk:
            correct_k = correct[:k].reshape(-1).float().sum(0, keepdim=True)
            res.append(correct_k.mul_(100.0 / batch_size))
        return res


if __name__ == '__main__':
    main()

    # python main_moco.py --data_dir ./data/mmwhs/ --do_contrast --dist-url 'tcp://localhost:10001'
    # --multiprocessing-distributed --world-size 1 --rank 0
