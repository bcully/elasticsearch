/*
 * Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
 * or more contributor license agreements. Licensed under the "Elastic License
 * 2.0", the "GNU Affero General Public License v3.0 only", and the "Server Side
 * Public License v 1"; you may not use this file except in compliance with, at
 * your election, the "Elastic License 2.0", the "GNU Affero General Public
 * License v3.0 only", or the "Server Side Public License, v 1".
 */

package org.elasticsearch.cluster.metadata;

import org.elasticsearch.cluster.metadata.IndexReshardingMetadata.SourceShardState;
import org.elasticsearch.cluster.metadata.IndexReshardingMetadata.TargetShardState;
import org.elasticsearch.common.io.stream.Writeable;
import org.elasticsearch.test.AbstractXContentSerializingTestCase;
import org.elasticsearch.xcontent.XContentParser;

import java.io.IOException;

public class IndexReshardingMetadataSerializationTests extends AbstractXContentSerializingTestCase<IndexReshardingMetadata> {
    @Override
    protected IndexReshardingMetadata doParseInstance(XContentParser parser) throws IOException {
        return IndexReshardingMetadata.fromXContent(parser);
    }

    @Override
    protected Writeable.Reader<IndexReshardingMetadata> instanceReader() {
        return IndexReshardingMetadata::new;
    }

    @Override
    protected IndexReshardingMetadata createTestInstance() {
        final int oldShards = randomIntBetween(1, 100);
        final int newShards = randomIntBetween(2, 5) * oldShards;
        final var sourceShardStates = new SourceShardState[oldShards];
        final var targetShardStates = new TargetShardState[newShards - oldShards];
        // Semantically it is illegal for SourceShardState to be DONE before all corresponding target shards are
        // DONE but these tests are exercising equals/hashcode not semantic integrity. Letting the source shard
        // state vary randomly gives better coverage in fewer instances even though it is wrong semantically.
        for (int i = 0; i < oldShards; i++) {
            sourceShardStates[i] = randomFrom(SourceShardState.values());
        }
        for (int i = 0; i < targetShardStates.length; i++) {
            targetShardStates[i] = randomFrom(TargetShardState.values());
        }

        return new IndexReshardingMetadata(oldShards, newShards, sourceShardStates, targetShardStates);
    }

    // To exercise equals() we want to mutate exactly one thing randomly so we know that each component
    // is contributing to equality testing. Some of these mutations are semantically illegal but we ignore
    // that here.
    @Override
    protected IndexReshardingMetadata mutateInstance(IndexReshardingMetadata instance) throws IOException {
        enum Mutation {
            OLD_SHARD_COUNT,
            NEW_SHARD_COUNT,
            SOURCE_SHARD_STATES,
            TARGET_SHARD_STATES
        }

        var oldShardCount = instance.oldShardCount();
        var newShardCount = instance.newShardCount();
        var sourceShardStates = instance.sourceShardStates().clone();
        var targetShardStates = instance.targetShardStates().clone();

        switch (randomFrom(Mutation.values())) {
            case OLD_SHARD_COUNT:
                oldShardCount++;
                break;
            case NEW_SHARD_COUNT:
                newShardCount++;
                break;
            case SOURCE_SHARD_STATES:
                var is = randomInt(sourceShardStates.length - 1);
                sourceShardStates[is] = SourceShardState.values()[(sourceShardStates[is].ordinal() + 1) % SourceShardState.values().length];
                break;
            case TARGET_SHARD_STATES:
                var it = randomInt(targetShardStates.length - 1);
                targetShardStates[it] = TargetShardState.values()[(targetShardStates[it].ordinal() + 1) % TargetShardState.values().length];
        }

        return new IndexReshardingMetadata(oldShardCount, newShardCount, sourceShardStates, targetShardStates);
    }
}
