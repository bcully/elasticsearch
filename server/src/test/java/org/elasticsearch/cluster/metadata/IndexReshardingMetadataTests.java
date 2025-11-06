/*
 * Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
 * or more contributor license agreements. Licensed under the "Elastic License
 * 2.0", the "GNU Affero General Public License v3.0 only", and the "Server Side
 * Public License v 1"; you may not use this file except in compliance with, at
 * your election, the "Elastic License 2.0", the "GNU Affero General Public
 * License v3.0 only", or the "Server Side Public License, v 1".
 */

package org.elasticsearch.cluster.metadata;

import org.elasticsearch.common.util.set.Sets;
import org.elasticsearch.test.ESTestCase;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;

public class IndexReshardingMetadataTests extends ESTestCase {
    public void testSplitBuckets() {
        final int routingNumShards = 12;
        final int initialNumShards = 3;
        final int splitShards = 6;
        final Map<Integer, Set<Integer>> buckets = HashMap.newHashMap(initialNumShards);
        for (int i = 0; i < splitShards; i++) {
            buckets.put(i, Sets.newHashSet());
        }
        for (int i = 0; i < routingNumShards; i++) {
//            int toShardId = Math.floorMod(i, routingNumShards) / routingFactor;
//            int splitShardId = (toShardId + (toShardId % splitFactor) * splitShards) / splitFactor;
            int splitShardId = calculateShardFFT(i, initialNumShards, routingNumShards, splitShards);
            var shard = buckets.get(splitShardId);
            assert shard != null : "no bucket for shard " + splitShardId;
            assert shard.contains(i) == false;
            shard.add(i);
        }

        for (int i = 0; i < splitShards; i++) {
            System.out.println("S" + i + " -> " + ofSet(buckets.get(i)));
        }
    }

    int calculateShardTB(int hash, int originalNumShards, int routingNumShards, int currentNumShards) {
        int routingShardId = Math.floorMod(hash, routingNumShards);

        // Start with original placement
        int originalRoutingFactor = routingNumShards / originalNumShards;
        int shard = routingShardId / originalRoutingFactor;

        // Apply each doubling split incrementally
        int numShards = originalNumShards;
        while (numShards < currentNumShards) {
            int routingFactor = routingNumShards / numShards;
            int positionInShard = routingShardId % routingFactor;
            int halfSize = routingFactor / 2;

            // If in second half, move to split-off shard
            if (positionInShard >= halfSize) {
                shard = shard + numShards;
            }

            numShards *= 2;
        }

        return shard;
    }

    public static int calculateShardTJ(int hash, int originalNumShards, int currentNumShards, int routingNumShards) {
        int timesSplit = Integer.numberOfTrailingZeros(currentNumShards / originalNumShards);
        int routingFactor = routingNumShards / currentNumShards;

        int rawShardId = Math.floorMod(hash, routingNumShards) / routingFactor;

        return bitReverse(rawShardId, timesSplit) * originalNumShards + (rawShardId >> timesSplit);
    }

    private static int bitReverse(int value, int numBits) {
        if (numBits == 0) {
            return 0;
        }

        // Reverse all 32 bits, then shift right to get only the numBits we care about
        return Integer.reverse(value) >>> (32 - numBits);
    }
    private int calculateShardFFT(int hash, int originalNumShards, int routingNumShards, int currentNumShards) {
        int routingFactor = routingNumShards / currentNumShards;
        int rawShardId = Math.floorMod(hash, routingNumShards) / routingFactor;
        int timesResplit = Integer.numberOfTrailingZeros(currentNumShards / originalNumShards);

        return bitReverse(rawShardId, timesResplit) * originalNumShards + (rawShardId >> timesResplit);
    }

    private int calculateShard(int hash, int originalNumShards, int routingNumShards, int currentNumShards) {
        int routingShardId = Math.floorMod(hash, routingNumShards);
        int originalRoutingFactor = routingNumShards / originalNumShards;

        int originalShard = routingShardId / originalRoutingFactor;
        int positionInOriginal = routingShardId % originalRoutingFactor;

        int splitFactor = currentNumShards / originalNumShards;
        int splitBucket = positionInOriginal / (originalRoutingFactor / splitFactor);

        return originalShard + (splitBucket * originalNumShards);
    }

    int calculateShard2(int hash, int originalNumShards, int currentNumShards, int routingNumShards) {
        int routingShardId = Math.floorMod(hash, routingNumShards);

        int originalRoutingFactor = routingNumShards / originalNumShards;
        int originalShard = routingShardId / originalRoutingFactor;
        int positionInOriginal = routingShardId % originalRoutingFactor;

        int splitDepth = Integer.numberOfTrailingZeros(currentNumShards / originalNumShards);
        int bitsToShift = Integer.numberOfTrailingZeros(originalRoutingFactor) - splitDepth;
        int splitOffset = positionInOriginal >> bitsToShift;

        return originalShard + (splitOffset * originalNumShards);
    }

    int calculateShardI(int hash, int originalNumShards, int currentNumShards, int routingNumShards) {
        int routingShardId = Math.floorMod(hash, routingNumShards);

        // Start with original placement
        int originalRoutingFactor = routingNumShards / originalNumShards;
        int shard = routingShardId / originalRoutingFactor;

        // Apply each doubling split incrementally
        int numShards = originalNumShards;
        while (numShards < currentNumShards) {
            int routingFactor = routingNumShards / numShards;
            int positionInShard = routingShardId % routingFactor;
            int halfSize = routingFactor / 2;

            // If in second half, move to split-off shard
            if (positionInShard >= halfSize) {
                shard = shard + numShards;
            }

            numShards *= 2;  // Double for next iteration
        }

        return shard;
    }

    private String ofSet(Set<Integer> set) {
        if (set.isEmpty()) {
            return "[]";
        }
        var sb = new StringBuilder();
        sb.append("[");
        var last = -100;
        var list = set.stream().sorted().toList();
        for (var v : set.stream().sorted().toList()) {
            if (v != last + 1) {
                if (last != -100) {
                    sb.append("..");
                    sb.append(last);
                    sb.append(",");
                }
                sb.append(v);
            }
            last = v;
        }
        sb.append("..");
        sb.append(list.getLast());
        sb.append("]");
        return sb.toString();
    }

    // test that we can drive a split through all valid state transitions in random order and terminate
    public void testSplit() {
        final var numShards = randomIntBetween(1, 10);
        final var multiple = randomIntBetween(2, 5);

        final var metadata = IndexReshardingMetadata.newSplitByMultiple(numShards, multiple);
        var split = metadata.getSplit();

        // starting state is as expected
        assertEquals(numShards, metadata.shardCountBefore());
        assertEquals(numShards * multiple, metadata.shardCountAfter());
        for (int i = 0; i < numShards; i++) {
            assertSame(IndexReshardingState.Split.SourceShardState.SOURCE, split.getSourceShardState(i));
            assertFalse(split.isTargetShard(i));
        }
        for (int i = numShards; i < numShards * multiple; i++) {
            assertSame(IndexReshardingState.Split.TargetShardState.CLONE, split.getTargetShardState(i));
            assertTrue(split.isTargetShard(i));
        }

        // advance split state randomly and expect to terminate
        while (true) {
            var splitBuilder = split.builder();
            // pick a shard at random and see if we can advance it
            int idx = randomIntBetween(0, numShards * multiple - 1);
            if (idx < numShards) {
                // can we advance source?
                var sourceState = split.getSourceShardState(idx);
                var nextState = randomFrom(IndexReshardingState.Split.SourceShardState.values());
                if (nextState.ordinal() == sourceState.ordinal() + 1) {
                    if (split.targetsDone(idx)) {
                        long ongoingSourceShards = split.sourceStates()
                            .filter(state -> state != IndexReshardingState.Split.SourceShardState.DONE)
                            .count();
                        // all source shards done is an invalid state, terminate
                        if (ongoingSourceShards == 1) break;
                        splitBuilder.setSourceShardState(idx, nextState);
                    } else {
                        assertThrows(AssertionError.class, () -> splitBuilder.setSourceShardState(idx, nextState));
                    }
                } else {
                    assertThrows(AssertionError.class, () -> splitBuilder.setSourceShardState(idx, nextState));
                }
            } else {
                // can we advance target?
                var targetState = split.getTargetShardState(idx);
                var nextState = randomFrom(IndexReshardingState.Split.TargetShardState.values());
                if (nextState.ordinal() == targetState.ordinal() + 1) {
                    splitBuilder.setTargetShardState(idx, nextState);
                } else {
                    assertThrows(AssertionError.class, () -> splitBuilder.setTargetShardState(idx, nextState));
                }
            }
            split = splitBuilder.build();
        }

        assertEquals(
            split.sourceStates().filter(state -> state == IndexReshardingState.Split.SourceShardState.DONE).count(),
            numShards - 1
        );
        for (int i = numShards; i < numShards * multiple; i++) {
            assertSame(IndexReshardingState.Split.TargetShardState.DONE, split.getTargetShardState(i));
            assertTrue(split.isTargetShard(i));
        }
    }
}
