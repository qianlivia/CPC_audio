import torch
import torch.nn as nn

import numpy as np


class PredictionNetwork(nn.Module):

    def __init__(self,
                 nPredicts,
                 dimOutputAR,
                 dimOutputEncoder):

        super(PredictionNetwork, self).__init__()
        self.predictors = nn.ModuleList()

        for i in range(nPredicts):

            self.predictors.append(
                nn.Linear(dimOutputAR, dimOutputEncoder, bias=False))

    def forward(self, c, candidates):

        assert(len(candidates) == len(self.predictors))

        out = []
        for k in range(len(self.predictors)):

            # torch.nn.Bilinear ? Replace
            locC = self.predictors[k](c)
            locC = locC.view(locC.size(0), 1, locC.size(1), locC.size(2))
            outK = (locC*candidates[k]).mean(dim=3)

            out.append(outK)
        return out


class CPCUnsupersivedCriterion(nn.Module):

    def __init__(self,
                 nPredicts,
                 dimOutputAR,
                 dimOutputEncoder,
                 negativeSamplingExt,
                 nGtSequence):

        super(CPCUnsupersivedCriterion, self).__init__()
        self.wPrediction = PredictionNetwork(
            nPredicts, dimOutputAR, dimOutputEncoder)
        self.nPredicts = nPredicts
        self.negativeSamplingExt = negativeSamplingExt
        self.nGtSequence = nGtSequence

        self.lossCriterion = nn.CrossEntropyLoss()

    def sample(self, gtPredictions, encodedData, windowSize):

        # Correct the number of negative samples to make sure that the number
        # of indices to draw is lower than the available number of indices
        dimEncoded = encodedData.size(1)
        nNegativeExt = encodedData.size(0)

        negativeSamplingExt = min(self.negativeSamplingExt, nNegativeExt)

        # The ground truth data will always be the first item
        labelLoss = torch.zeros((windowSize),
                                dtype=torch.long,
                                device=encodedData.device)

        if negativeSamplingExt > 0:
            extIdx = np.random.randint(0, nNegativeExt,
                                       size=(negativeSamplingExt
                                             * windowSize
                                             * self.nGtSequence))
            negExt = encodedData[extIdx].view(self.nGtSequence,
                                              negativeSamplingExt,
                                              windowSize,
                                              dimEncoded)
        else:
            negExt = encodedData.view(-1, 1, dimEncoded).expand(-1,
                                                                windowSize,
                                                                dimEncoded)
            negExt = negExt.view(1, -1, windowSize, dimEncoded
                                 ).expand(self.nGtSequence, -1, windowSize,
                                          dimEncoded)

        outputs = []
        for k in range(1, self.nPredicts + 1):

            # Positive samples
            if k < self.nPredicts:
                posSeq = gtPredictions[:, k:-(self.nPredicts-k)]
            else:
                posSeq = gtPredictions[:, k:]

            posSeq = posSeq.view(posSeq.size(
                0), 1, posSeq.size(1), posSeq.size(2))

            # Full sequence
            fullSeq = torch.cat((posSeq, negExt), dim=1)
            outputs.append(fullSeq)

        return outputs, labelLoss

    def forward(self, cFeature, gtPredictions, otherEncoded, *args):
        windowSize = gtPredictions.size(1) - self.nPredicts
        cFeature = cFeature[:, :windowSize]
        sampledData, labelLoss = self.sample(
            gtPredictions, otherEncoded, windowSize)

        predictions = self.wPrediction(cFeature, sampledData)

        outLosses = [0 for x in range(self.nPredicts)]
        outAcc = [0 for x in range(self.nPredicts)]

        for k, locPreds in enumerate(predictions):
            locPreds = locPreds.permute(0, 2, 1)
            for gtSeq in range(self.nGtSequence):
                lossK = self.lossCriterion(locPreds[gtSeq], labelLoss)
                outLosses[k] += lossK.view(-1) / self.nGtSequence
                _, predsIndex = locPreds[gtSeq].max(1)
                outAcc[k] += torch.sum(predsIndex == 0).double(
                ).view(-1) / (self.nGtSequence * windowSize)

        return torch.cat(outLosses, dim=0), torch.cat(outAcc, dim=0)


class SpeakerCriterion(nn.Module):

    def __init__(self, dimEncoder, nSpeakers, nSample):

        super(SpeakerCriterion, self).__init__()

        self.linearSpeakerClassifier = nn.Linear(
            dimEncoder * nSample, nSpeakers)
        self.lossCriterion = nn.CrossEntropyLoss()
        self.nGtSequence = -1
        self.nSample = nSample

    def forward(self, cFeature, gtPredictions, otherEncoded, label):

        # cFeature.size() : batchSize x seq Size x hidden size
        batchSize = cFeature.size(0)
        cFeature = cFeature[:, -1, :]
        cFeature = cFeature.view(batchSize, -1)

        predictions = self.linearSpeakerClassifier(cFeature)
        loss = self.lossCriterion(predictions, label).view(-1)

        acc = (predictions.max(1)[1] == label).double().mean().view(-1)

        return loss, acc


class PhoneCriterion(nn.Module):

    def __init__(self, dimEncoder, nPhones):

        super(PhoneCriterion, self).__init__()

        self.PhoneCriterionClassifier = nn.Linear(
            dimEncoder, nPhones)
        self.lossCriterion = nn.CrossEntropyLoss()
        self.nGtSequence = -1

    def forward(self, cFeature, gtPredictions, otherEncoded, label):

        # cFeature.size() : batchSize x seq Size x hidden size
        batchSize, seqSize = cFeature.size(0), cFeature.size(1)
        cFeature = cFeature.contiguous().view(batchSize * seqSize, -1)
        label = label.view(-1)

        predictions = self.PhoneCriterionClassifier(cFeature)
        loss = self.lossCriterion(predictions, label).view(-1)

        acc = (predictions.max(1)[1] == label).double().mean().view(-1)
        return loss, acc
