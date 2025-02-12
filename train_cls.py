"""
Author: Young
Date: January 2021
"""
from data_utils.ModelNetDataLoader import ModelNetDataLoader
import argparse
import numpy as np
import os
import torch
import datetime
import logging
from pathlib import Path
from tqdm import tqdm
import sys
import provider
import importlib
import shutil

# 找出相对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = BASE_DIR
sys.path.append(os.path.join(ROOT_DIR, 'models'))


def parse_args():
    '''PARAMETERS'''
    parser = argparse.ArgumentParser('PointNet')
    # -- 表示可选参数，设置required参数表示必须
    parser.add_argument('--batch_size', type=int, default=48, help='batch size in training [default: 24]')
    parser.add_argument('--model', default='pointnet_cls', help='model name [default: pointnet_cls]')
    parser.add_argument('--epoch', default=20, type=int, help='number of epoch in training [default: 200]')
    parser.add_argument('--learning_rate', default=0.001, type=float, help='learning rate in training [default: 0.001]')
    parser.add_argument('--gpu', type=str, default='0', help='specify gpu device [default: 0]')
    parser.add_argument('--num_point', type=int, default=1024, help='Point Number [default: 1024]')
    parser.add_argument('--optimizer', type=str, default='Adam', help='optimizer for training [default: Adam]')
    parser.add_argument('--log_dir', type=str, default=None, help='experiment root')
    parser.add_argument('--decay_rate', type=float, default=1e-4, help='decay rate [default: 1e-4]')
    parser.add_argument('--normal', action='store_true', default=False,
                        help='Whether to use normal information [default: False]')
    # parse_args解析参数后才能使用arg
    return parser.parse_args()


def test(model, loader, num_class=40):
    mean_correct = []
    # shape （40，3）
    # class_acc[cat, 0]：(预测正确个数/该类在batch size中所占的size)，循环完毕后是test中所有batch size中类别的平均accuracy
    # class_acc[cat, 1]：记录该test数据集中每一个类别的个数
    # class_acc[cat, 2]：所有输入的test数据集中一个类别正确的accuracy(正确个数 / batch size / test中一类的个数)
    class_acc = np.zeros((num_class, 3))
    # 对batch size进行循环
    for j, data in tqdm(enumerate(loader), total=len(loader)):
        points, target = data
        target = target[:, 0]
        points = points.transpose(2, 1)
        points, target = points.cuda(), target.cuda()
        # eval模式不使用dropout和BN
        classifier = model.eval()
        # 得到预测
        pred, _ = classifier(points)
        # 得到最大预测值max(1)[1]返回维度1最大值的索引
        pred_choice = pred.data.max(1)[1]
        # 在一个batch size中的数据根据种类（label）进行遍历
        for cat in np.unique(target.cpu()):
            # 找出每一类的accuracy
            # pred_choice[target == cat] :找出某一类的预测值
            # 将预测pred_choice和target（label）比较返回Boolean，将正确的求和算出预测正确的个数
            classacc = pred_choice[target == cat].eq(target[target == cat].long().data).cpu().sum()
            # 记录一个batch size中类别的平均accuracy(预测正确个数 / 该类在batch size中所占的size)，循环完毕后是test中所有batch size中类别的平均accuracy
            class_acc[cat, 0] += classacc.item() / float(points[target == cat].size()[0])
            # 记录该batch size中每一个类别的个数
            class_acc[cat, 1] += 1
        # 统计所有batch size样本中正确的数量（包含所有类别，上面for只是统计每一个类别）
        correct = pred_choice.eq(target.long().data).cpu().sum()
        # 记录一个batch size的平均正确率
        mean_correct.append(correct.item() / float(points.size()[0]))
    # 计算test数据集中该类的平均accuracy
    class_acc[:, 2] = class_acc[:, 0] / class_acc[:, 1]
    # 计算所有test数据集中每个单一类别类别的平均accuracy
    class_acc = np.mean(class_acc[:, 2])
    # 计算所有类别的平均正确率
    instance_acc = np.mean(mean_correct)
    # 返回所有类别的平均正确率instance_acc，和每个单一类别的平均正确率class_acc
    return instance_acc, class_acc


def main(args):
    # 定义输出log和输出console
    def log_string(str):
        logger.info(str)
        print(str)

    '''HYPER PARAMETER'''
    os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu  # 设置GPU的编号（可以多GPU运行）

    '''CREATE DIR'''
    timestr = str(datetime.datetime.now().strftime('%Y-%m-%d_%H-%M'))  # 获取当前时间
    experiment_dir = Path('./log/')  # 对目录初始化path类
    experiment_dir.mkdir(exist_ok=True)  # 创建目录./log/，exist_ok=True目录存在不报错
    # 创建classification目录
    experiment_dir = experiment_dir.joinpath('classification')
    experiment_dir.mkdir(exist_ok=True)  # 目录存在不报错
    if args.log_dir is None:
        experiment_dir = experiment_dir.joinpath(timestr)
    else:
        experiment_dir = experiment_dir.joinpath(args.log_dir)
    experiment_dir.mkdir(exist_ok=True)
    checkpoints_dir = experiment_dir.joinpath('checkpoints/')  # 创建checkpoints目录
    checkpoints_dir.mkdir(exist_ok=True)
    log_dir = experiment_dir.joinpath('logs/')
    log_dir.mkdir(exist_ok=True)

    '''LOG'''
    args = parse_args()
    logger = logging.getLogger("Model")
    # 设置日志级别info : 打印info,warning,error,critical级别的日志
    logger.setLevel(logging.INFO)
    # 配置日志的格式
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # 格式化字符串，路径为log_dir/args.model.txt
    file_handler = logging.FileHandler('%s/%s.txt' % (log_dir, args.model))
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    log_string('PARAMETER ...')
    log_string(args)

    '''DATA LOADING'''
    log_string('Load dataset ...')
    DATA_PATH = 'data/modelnet40_normal_resampled/'

    TRAIN_DATASET = ModelNetDataLoader(root=DATA_PATH, npoint=args.num_point, split='train',
                                       normal_channel=args.normal)
    TEST_DATASET = ModelNetDataLoader(root=DATA_PATH, npoint=args.num_point, split='test',
                                      normal_channel=args.normal)
    import torch.utils.data.dataloader
    # 读取TRAIN_DATASET，设置batch_size,shuffle=True，打乱顺序，num_workers多线程
    trainDataLoader = torch.utils.data.DataLoader(TRAIN_DATASET, batch_size=args.batch_size, shuffle=True,
                                                  num_workers=4)
    # test数据集不需要打乱顺序
    testDataLoader = torch.utils.data.DataLoader(TEST_DATASET, batch_size=args.batch_size, shuffle=False, num_workers=4)

    '''MODEL LOADING'''
    num_class = 40
    # 导入model模型,相当于 import model
    MODEL = importlib.import_module(args.model)
    # 拷贝文件和权限，拷贝到experiment_dir='log\\classification\\pointnet2_cls_msg'
    shutil.copy('./models/%s.py' % args.model, str(experiment_dir))
    shutil.copy('./models/pointnet_util.py', str(experiment_dir))

    # 调用model(如pointnet_cls.py)中的方法
    classifier = MODEL.get_model(num_class, normal_channel=args.normal).cuda()
    criterion = MODEL.get_loss().cuda()

    # 间断后继续训练
    try:
        # 读取pth文件，pth中都以字典存储
        # 将每一层与它的对应参数建立映射关系.(如model的每一层的weights及偏置等等)储存
        # (注意,只有那些参数可以训练的layer才会被保存到模型的state_dict中,如卷积层,线性层等等)
        # 优化器对象Optimizer也有一个state_dict,它包含了优化器的状态以及被使用的超参数(如lr, momentum,weight_decay等)
        checkpoint = torch.load(str(experiment_dir) + '/checkpoints/best_model.pth')
        # 读取epoch
        start_epoch = checkpoint['epoch']
        # 冲checkpoint中读取model_state_dict，并且用load_state_dict恢复模型参数
        classifier.load_state_dict(checkpoint['model_state_dict'])
        # 写入log
        log_string('Use pretrain model')
    except:
        log_string('No existing model, starting training from scratch...')
        start_epoch = 0

    # 创建optimizer优化器对象，这个对象能够保持当前参数状态并基于计算得到的梯度进行参数更新
    if args.optimizer == 'Adam':
        optimizer = torch.optim.Adam(
            # 输入参数
            classifier.parameters(),
            lr=args.learning_rate,
            betas=(0.9, 0.999),
            eps=1e-08,
            weight_decay=args.decay_rate
        )
    else:
        optimizer = torch.optim.SGD(classifier.parameters(), lr=0.01, momentum=0.9)
    # todo: https://blog.csdn.net/qyhaill/article/details/103043637
    # 每过step_size个epoch，做一次更新
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=20, gamma=0.7)
    global_epoch = 0
    global_step = 0
    best_instance_acc = 0.0
    best_class_acc = 0.0
    mean_correct = []

    '''TRANING'''
    # 输出log
    logger.info('Start training...')
    for epoch in range(start_epoch, args.epoch):
        log_string('Epoch %d (%d/%s):' % (global_epoch + 1, epoch + 1, args.epoch))

        # 更新lr，在epoch处调整
        scheduler.step()
        # total迭代总次数，默认为迭代元素的长度，smoothing：0-1，0平均速度，1当前速度
        for batch_id, data in tqdm(enumerate(trainDataLoader, 0), total=len(trainDataLoader), smoothing=0.9):
            # 从trainDataLoader中提取points：点集，target：对应的目标
            points, target = data
            # 将tensor转化为numpy
            points = points.data.numpy()
            '''数据增强模块'''
            # 随机丢点
            points = provider.random_point_dropout(points)
            # 随机范围
            points[:, :, 0:3] = provider.random_scale_point_cloud(points[:, :, 0:3])
            # 随机移动点云
            points[:, :, 0:3] = provider.shift_point_cloud(points[:, :, 0:3])
            # 转化为tensor
            points = torch.Tensor(points)
            # 得到target
            target = target[:, 0]
            # 输入要求（N，3）因此转置
            points = points.transpose(2, 1)
            # 存入显存
            points, target = points.cuda(), target.cuda()
            # 梯度归零
            optimizer.zero_grad()
            # 训练模式
            classifier = classifier.train()
            # 得到pointnet_cls前向传播返回的两个数据
            pred, trans_feat = classifier(points)
            # loss
            loss = criterion(pred, target.long(), trans_feat)
            # 得到最大值的index
            pred_choice = pred.data.max(1)[1]
            # 将预测pred_choice和target（label）比较返回Boolean，将正确的求和算出预测正确的个数
            correct = pred_choice.eq(target.long().data).cpu().sum()
            # correct.item()提取出tensor中的元素，将正确率其添加到mean_correct数组中
            mean_correct.append(correct.item() / float(points.size()[0]))
            # 反向传播
            loss.backward()
            # 更新梯度
            optimizer.step()
            global_step += 1

        train_instance_acc = np.mean(mean_correct)
        log_string('Train Instance Accuracy: %f' % train_instance_acc)

        # 在这个block（with torch.no_grad():）中不需要计算梯度（test模式）
        with torch.no_grad():
            # classifier.eval()：在test模式中禁用dropout和BN
            instance_acc, class_acc = test(classifier.eval(), testDataLoader)

            if (instance_acc >= best_instance_acc):
                best_instance_acc = instance_acc
                best_epoch = epoch + 1

            if (class_acc >= best_class_acc):
                best_class_acc = class_acc
            log_string('Test Instance Accuracy: %f, Class Accuracy: %f' % (instance_acc, class_acc))
            log_string('Best Instance Accuracy: %f, Class Accuracy: %f' % (best_instance_acc, best_class_acc))

            if (instance_acc >= best_instance_acc):
                logger.info('Save model...')
                savepath = str(checkpoints_dir) + '/best_model.pth'
                log_string('Saving at %s' % savepath)
                state = {
                    'epoch': best_epoch,
                    'instance_acc': instance_acc,
                    'class_acc': class_acc,
                    'model_state_dict': classifier.state_dict(),
                    'optimizer_state_dict': optimizer.state_dict(),
                }
                torch.save(state, savepath)
            global_epoch += 1

    logger.info('End of training...')


if __name__ == '__main__':
    args = parse_args()
    main(args)
